"""Adversarial review tests for the dataset-reuse work (G2/G3/G4).

Written by the final reviewer against the *seams* the feature tests don't
cover — each one targets a place where the implementation could plausibly
have regressed something it merely passed through:

1. `_persist_jsonl_dataset` grew three new keyword parameters (`source`,
   `key_prefix`, `audit_action`) so `upload_dataset` could reuse it. Those
   defaults are load-bearing: ~4 pre-existing direct callers (tests
   included) never pass them. A wrong default would silently relabel every
   seed upload as `uploaded`, write it to the wrong MinIO prefix, or emit
   the wrong audit action. Pinned here explicitly.

2. `POST /datasets/upload` reuses the seed pipeline's canonical validator.
   Rows that are *well-formed JSON and valid for a different task type*
   must still be rejected — a classification file uploaded to a QA project
   is the realistic user error, and "valid JSON" is not "valid for this
   task".

3. Training on a `source='uploaded'` dataset must work through the real
   submit path (not just be reachable in the schema), for manual *and* HPO.

4. `Dataset.seed_dataset_id` is `ondelete='SET NULL'`, unlike
   `parent_dataset_id`'s CASCADE. Deleting a seed must orphan the reference
   rather than delete the generated child. Exercised against a real FK with
   `PRAGMA foreign_keys=ON` so the DDL — not just the Python-side comment —
   is what's under test.

5. G3's ownership gate on an *orphaned* (`project_id IS NULL`) uploaded
   dataset: fail-closed 403 for a non-owner under auth-on, allowed for the
   owner, and 403 for a null-owner row — on both the manual and HPO paths.

In-memory aiosqlite; no Postgres, no Docker, no GPU, no real broker.
"""

from __future__ import annotations

import json
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from api.core import request_context
from api.core.auth import CurrentUser
from api.core.config import get_settings
from api.models.audit_event import AuditEvent
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.project import Project
from api.schemas.enums import DatasetSource, JobStatus, TaskType
from api.schemas.training import (
    HPOConfig,
    HPOFloatRange,
    HPOSearchSpace,
    HPOTrainingRequest,
    ManualTrainingRequest,
)
from api.schemas.upload import FormatDetectionReport
from api.services import datasets_service as ds_module
from api.services import training_service


# KNOWN GOTCHA: Dataset/Project use postgresql.JSONB, which sqlite has no
# native type for (see tests/unit/test_dataset_status.py).
@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


ALICE = CurrentUser(id="alice-sub", email="alice@example.com")
BOB = CurrentUser(id="bob-sub", email="bob@example.com")


@pytest.fixture
async def db():
    """In-memory aiosqlite engine + AsyncSession with the full ORM schema."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def fk_db():
    """Same as `db`, but with sqlite's FK enforcement actually turned on.

    sqlite ignores `ON DELETE SET NULL` unless `PRAGMA foreign_keys=ON` is
    set per-connection — without this the SET NULL test below would pass
    vacuously (nothing would be enforced, and the child row would keep a
    dangling id rather than being nulled).

    `StaticPool` is what makes a single `PRAGMA` here cover the session too:
    the pragma is per-connection, and StaticPool hands every checkout the
    same underlying connection. (Setting it from a sync ``connect`` event
    listener instead raises `MissingGreenlet` — aiosqlite's cursor is async
    and that hook is not running in a greenlet context.)
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", future=True, poolclass=StaticPool
    )

    async with engine.begin() as conn:
        await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def _clear_request_context():
    yield
    request_context.clear()


@pytest.fixture
def spy_apply(monkeypatch):
    """Patch `apply_async` on both training Celery tasks — no real broker."""
    calls: dict[str, list[dict]] = {"training": [], "hpo": []}
    counter = {"n": 0}

    class _Result:
        def __init__(self, job_id: str) -> None:
            self.id = job_id

    def _make(name):
        def _apply_async(**kwargs):
            calls[name].append(kwargs)
            counter["n"] += 1
            return _Result(f"job-{name}-{counter['n']}")

        return _apply_async

    import workers.tasks.hpo_training as hpo_task
    import workers.tasks.training as training_task

    monkeypatch.setattr(training_task.train_manual, "apply_async", _make("training"))
    monkeypatch.setattr(hpo_task.train_hpo, "apply_async", _make("hpo"))
    return calls


async def _make_project(
    db: AsyncSession, *, owner_id: str | None, task_type: TaskType = TaskType.QA
) -> Project:
    project = Project(id=uuid4(), name="proj", task_type=task_type, owner_id=owner_id)
    db.add(project)
    await db.flush()
    return project


async def _make_dataset(
    db: AsyncSession,
    *,
    project_id,
    owner_id: str | None,
    source: DatasetSource = DatasetSource.UPLOADED,
    task_type: TaskType = TaskType.QA,
    storage_uri: str | None = "s3://datasets/uploads/ds.jsonl",
    num_samples: int = 5,
    seed_dataset_id=None,
) -> Dataset:
    dataset = Dataset(
        id=uuid4(),
        project_id=project_id,
        owner_id=owner_id,
        name="ds",
        task_type=task_type,
        source=source,
        status=JobStatus.COMPLETED,
        num_samples=num_samples,
        storage_uri=storage_uri,
        seed_dataset_id=seed_dataset_id,
    )
    db.add(dataset)
    await db.flush()
    return dataset


def _hpo_request(*, project_id, dataset_id):
    return HPOTrainingRequest(
        project_id=project_id,
        dataset_id=dataset_id,
        hpo_config=HPOConfig(
            search_space=HPOSearchSpace(
                learning_rate=HPOFloatRange(low=1e-5, high=5e-4, log=True)
            ),
        ),
    )


def _jsonl_file(rows: list[dict], filename: str = "data.jsonl") -> UploadFile:
    body = "\n".join(json.dumps(r) for r in rows).encode("utf-8")
    return UploadFile(file=BytesIO(body), filename=filename)


# =============================================================================
# 1. _persist_jsonl_dataset defaults — the seed path must be untouched
# =============================================================================


class TestPersistDefaultsUnchanged:
    """The parameterisation added for `upload_dataset` must be invisible to
    every caller that doesn't opt in. Pins all three defaults at once."""

    async def test_direct_call_without_new_kwargs_still_persists_a_seed(
        self, db: AsyncSession, fake_minio, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ds_module, "get_minio_client", lambda: fake_minio)
        project = await _make_project(db, owner_id="owner-1")

        fd_report = FormatDetectionReport(
            ran=False,
            model_used=None,
            field_mapping={},
            rows_total=1,
            rows_canonicalised=1,
            rows_dropped=0,
            notes="already canonical",
        )
        dataset = await ds_module._persist_jsonl_dataset(
            db,
            settings=get_settings(),
            project=project,
            task_type=TaskType.QA,
            name=None,  # exercise the generated-name default too
            valid_rows=[{"question": "q1", "answer": "a1"}],
            fd_report=fd_report,
        )

        assert dataset.source is DatasetSource.SEED, "default source must stay SEED"
        assert "/seeds/" in dataset.storage_uri, dataset.storage_uri
        assert "/uploads/" not in dataset.storage_uri
        assert dataset.name.startswith("seed-"), dataset.name

        actions = [
            a.action for a in (await db.execute(select(AuditEvent))).scalars().all()
        ]
        assert actions == ["dataset.seed_upload"], actions


# =============================================================================
# 2. Upload rows that are valid JSON, but for the WRONG task type
# =============================================================================


class TestUploadWrongTaskTypeRows:
    async def test_classification_rows_uploaded_to_qa_project_are_rejected(
        self, db: AsyncSession, fake_minio, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`{"text": ..., "label": ...}` is a perfectly valid classification
        row and perfectly invalid QA. The upload must 400 rather than
        persist an untrainable dataset the user only discovers at train
        time."""
        monkeypatch.setattr(ds_module, "get_minio_client", lambda: fake_minio)
        project = await _make_project(db, owner_id="owner-1", task_type=TaskType.QA)

        rows = [
            {"text": "the package arrived broken", "label": "complaint"},
            {"text": "thanks, works great", "label": "praise"},
        ]
        with pytest.raises(HTTPException) as exc:
            await ds_module.upload_dataset(
                db,
                project_id=project.id,
                task_type=TaskType.QA,
                name=None,
                file=_jsonl_file(rows),
                user=None,
            )
        assert exc.value.status_code == 400

        # ...and nothing was persisted on the way out.
        rows_after = (await db.execute(select(Dataset))).scalars().all()
        assert rows_after == []

    async def test_qa_rows_uploaded_to_classification_project_are_rejected(
        self, db: AsyncSession, fake_minio, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The mirror image, so this isn't just testing one validator."""
        monkeypatch.setattr(ds_module, "get_minio_client", lambda: fake_minio)
        project = await _make_project(
            db, owner_id="owner-1", task_type=TaskType.CLASSIFICATION
        )

        rows = [
            {"question": "What is the return window?", "answer": "30 days"},
            {"question": "Do you ship abroad?", "answer": "Yes"},
        ]
        with pytest.raises(HTTPException) as exc:
            await ds_module.upload_dataset(
                db,
                project_id=project.id,
                task_type=TaskType.CLASSIFICATION,
                name=None,
                file=_jsonl_file(rows),
                user=None,
            )
        assert exc.value.status_code in (400, 422)
        assert (await db.execute(select(Dataset))).scalars().all() == []


# =============================================================================
# 3. Training end-to-end on a source='uploaded' dataset
# =============================================================================


class TestTrainOnUploadedDataset:
    async def test_manual_submit_accepts_uploaded_source(
        self, db: AsyncSession, spy_apply
    ) -> None:
        project = await _make_project(db, owner_id="owner-a")
        dataset = await _make_dataset(
            db, project_id=project.id, owner_id="owner-a", source=DatasetSource.UPLOADED
        )

        resp = await training_service.submit_manual_training_job(
            db,
            ManualTrainingRequest(project_id=project.id, dataset_id=dataset.id),
            user=None,
        )
        assert resp.status is JobStatus.PENDING
        assert len(spy_apply["training"]) == 1

    async def test_hpo_submit_accepts_uploaded_source(
        self, db: AsyncSession, spy_apply
    ) -> None:
        project = await _make_project(db, owner_id="owner-a")
        dataset = await _make_dataset(
            db, project_id=project.id, owner_id="owner-a", source=DatasetSource.UPLOADED
        )

        resp = await training_service.submit_hpo_training_job(
            db, _hpo_request(project_id=project.id, dataset_id=dataset.id), user=None
        )
        assert resp.status is JobStatus.PENDING
        assert len(spy_apply["hpo"]) == 1

    async def test_uploaded_dataset_with_wrong_task_type_still_400s(
        self, db: AsyncSession, spy_apply
    ) -> None:
        """The relaxed project gate must not have relaxed the task_type gate."""
        project = await _make_project(db, owner_id="owner-a", task_type=TaskType.QA)
        dataset = await _make_dataset(
            db,
            project_id=project.id,
            owner_id="owner-a",
            source=DatasetSource.UPLOADED,
            task_type=TaskType.CLASSIFICATION,
        )

        with pytest.raises(HTTPException) as exc:
            await training_service.submit_manual_training_job(
                db,
                ManualTrainingRequest(project_id=project.id, dataset_id=dataset.id),
                user=None,
            )
        assert exc.value.status_code == 400
        assert spy_apply["training"] == []


# =============================================================================
# 4. seed_dataset_id is SET NULL, not CASCADE
# =============================================================================


class TestSeedDeletionSetsNull:
    async def test_deleting_the_seed_nulls_the_reference_and_keeps_the_child(
        self, fk_db: AsyncSession
    ) -> None:
        project = await _make_project(fk_db, owner_id="owner-1")
        seed = await _make_dataset(
            fk_db, project_id=project.id, owner_id="owner-1", source=DatasetSource.SEED
        )
        child = await _make_dataset(
            fk_db,
            project_id=project.id,
            owner_id="owner-1",
            source=DatasetSource.SDG,
            seed_dataset_id=seed.id,
        )
        await fk_db.commit()
        # Grab the ids BEFORE expiring: reading `child.id` off an expired
        # instance would trigger a synchronous refresh outside the greenlet
        # context and blow up with MissingGreenlet.
        seed_id, child_id = seed.id, child.id

        # Core (not ORM-unit-of-work) DELETE, so the DB's own FK action is
        # what's under test rather than SQLAlchemy relationship bookkeeping.
        # Typed construct rather than raw SQL on purpose: `Dataset.id` is a
        # `Uuid` rendered as CHAR(32) on sqlite (hex, no dashes), so a
        # hand-written `WHERE id = '<dashed-uuid>'` silently matches zero
        # rows and the test would pass for the wrong reason.
        await fk_db.execute(
            sa_delete(Dataset).where(Dataset.id == seed_id),
            execution_options={"synchronize_session": False},
        )
        await fk_db.commit()
        fk_db.expire_all()

        surviving = await fk_db.get(Dataset, child_id)
        assert surviving is not None, "SET NULL must not delete the generated child"
        assert surviving.seed_dataset_id is None, "dangling seed ref must be nulled"

        assert await fk_db.get(Dataset, seed_id) is None


# =============================================================================
# 5. G3 ownership on an orphaned uploaded dataset (auth-on)
# =============================================================================


class TestOrphanedUploadedDatasetOwnership:
    async def test_non_owner_gets_403_manual(self, db: AsyncSession, spy_apply) -> None:
        """Alice's orphaned upload, Bob's project, Bob authenticated."""
        bob_project = await _make_project(db, owner_id=BOB.id)
        alice_orphan = await _make_dataset(db, project_id=None, owner_id=ALICE.id)

        with pytest.raises(HTTPException) as exc:
            await training_service.submit_manual_training_job(
                db,
                ManualTrainingRequest(
                    project_id=bob_project.id, dataset_id=alice_orphan.id
                ),
                user=BOB,
            )
        assert exc.value.status_code == 403
        assert spy_apply["training"] == []

    async def test_non_owner_gets_403_hpo(self, db: AsyncSession, spy_apply) -> None:
        bob_project = await _make_project(db, owner_id=BOB.id)
        alice_orphan = await _make_dataset(db, project_id=None, owner_id=ALICE.id)

        with pytest.raises(HTTPException) as exc:
            await training_service.submit_hpo_training_job(
                db,
                _hpo_request(project_id=bob_project.id, dataset_id=alice_orphan.id),
                user=BOB,
            )
        assert exc.value.status_code == 403
        assert spy_apply["hpo"] == []

    async def test_owner_may_train_on_own_orphaned_upload(
        self, db: AsyncSession, spy_apply
    ) -> None:
        alice_project = await _make_project(db, owner_id=ALICE.id)
        alice_orphan = await _make_dataset(db, project_id=None, owner_id=ALICE.id)

        resp = await training_service.submit_manual_training_job(
            db,
            ManualTrainingRequest(
                project_id=alice_project.id, dataset_id=alice_orphan.id
            ),
            user=ALICE,
        )
        assert resp.status is JobStatus.PENDING
        assert len(spy_apply["training"]) == 1

    async def test_null_owner_orphan_fails_closed(
        self, db: AsyncSession, spy_apply
    ) -> None:
        """A pre-auth row owned by nobody is invisible to everyone, not
        public (ADR-012)."""
        alice_project = await _make_project(db, owner_id=ALICE.id)
        nobodys_orphan = await _make_dataset(db, project_id=None, owner_id=None)

        with pytest.raises(HTTPException) as exc:
            await training_service.submit_manual_training_job(
                db,
                ManualTrainingRequest(
                    project_id=alice_project.id, dataset_id=nobodys_orphan.id
                ),
                user=ALICE,
            )
        assert exc.value.status_code == 403
        assert spy_apply["training"] == []

    async def test_auth_off_may_use_any_orphan(
        self, db: AsyncSession, spy_apply
    ) -> None:
        """`user=None` is a complete no-op — phase-1 behaviour preserved."""
        project = await _make_project(db, owner_id=BOB.id)
        alice_orphan = await _make_dataset(db, project_id=None, owner_id=ALICE.id)

        resp = await training_service.submit_manual_training_job(
            db,
            ManualTrainingRequest(project_id=project.id, dataset_id=alice_orphan.id),
            user=None,
        )
        assert resp.status is JobStatus.PENDING
        assert len(spy_apply["training"]) == 1
