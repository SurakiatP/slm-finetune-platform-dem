"""Unit tests for G3: training on cross-project + orphaned datasets.

User decision G3 replaces the old "dataset must belong to the same project"
400 gate in `training_service.submit_manual_training_job` /
`submit_hpo_training_job` with an ownership check
(`ownership.assert_dataset_access`):

  * `user=None` (auth off, or a direct/legacy caller that doesn't pass one)
    is a complete no-op — any existing dataset, from any project or
    orphaned, may be used. This is what keeps the ~25 pre-existing direct
    callers of these two functions (which never passed `user`) compiling
    and passing unchanged.
  * An authenticated `user` may only train against a dataset they own
    (`Dataset.owner_id == user.id`), regardless of which project the
    dataset currently belongs to (cross-project reuse) or whether it has
    no project at all (`project_id IS NULL`, orphaned — its owner_id
    survives the orphaning, see `ownership.py`'s module docstring).
  * `Dataset.owner_id IS NULL` fails closed for an authenticated caller —
    403, not "public" — same rule as every other ownership check in this
    codebase (ADR-012).

The task_type-mismatch (400) and not-ready storage_uri/num_samples (409)
gates are unchanged and still run after the ownership check.

In-memory aiosqlite; no Postgres, no Docker, no GPU, no real Celery broker.
Follows the sqlite JSONB `@compiles` shim + `spy_apply` monkeypatch pattern
from `tests/unit/test_training_create_contract.py:35-68` (Project/Dataset/
TrainingJob use `sqlalchemy.dialects.postgresql.JSONB`, which sqlite has no
native type for).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from api.core import request_context
from api.core.auth import CurrentUser
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
from api.services import training_service


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


@pytest.fixture(autouse=True)
def _clear_request_context():
    """Keep the `request_context` contextvars (actor id) from leaking
    between tests — matches `test_training_create_contract.py`'s fixture."""
    yield
    request_context.clear()


@pytest.fixture
def spy_apply(monkeypatch):
    """Patch `apply_async` on both training Celery tasks so no real broker
    is touched, and record calls so 'nothing enqueued on a rejected path'
    is provable rather than assumed."""
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
    task_type: TaskType = TaskType.QA,
    storage_uri: str | None = "s3://datasets/ds.jsonl",
    num_samples: int = 5,
) -> Dataset:
    dataset = Dataset(
        id=uuid4(),
        project_id=project_id,
        owner_id=owner_id,
        name="ds",
        task_type=task_type,
        source=DatasetSource.SDG,
        status=JobStatus.COMPLETED,
        num_samples=num_samples,
        storage_uri=storage_uri,
    )
    db.add(dataset)
    await db.flush()
    return dataset


def _hpo_request(*, project_id, dataset_id, training_name=None):
    return HPOTrainingRequest(
        project_id=project_id,
        dataset_id=dataset_id,
        training_name=training_name,
        hpo_config=HPOConfig(
            search_space=HPOSearchSpace(
                learning_rate=HPOFloatRange(low=1e-5, high=5e-4, log=True)
            ),
        ),
    )


# =============================================================================
# Cross-project dataset reuse
# =============================================================================


class TestCrossProjectDatasetReuse:
    async def test_manual_dataset_from_project_b_submitted_to_project_a_succeeds(
        self, db: AsyncSession, spy_apply
    ) -> None:
        project_a = await _make_project(db, owner_id="owner-a")
        project_b = await _make_project(db, owner_id="owner-a")
        dataset_b = await _make_dataset(db, project_id=project_b.id, owner_id="owner-a")

        resp = await training_service.submit_manual_training_job(
            db,
            ManualTrainingRequest(project_id=project_a.id, dataset_id=dataset_b.id),
            user=None,
        )
        assert resp.status is JobStatus.PENDING
        assert len(spy_apply["training"]) == 1

    async def test_hpo_dataset_from_project_b_submitted_to_project_a_succeeds(
        self, db: AsyncSession, spy_apply
    ) -> None:
        project_a = await _make_project(db, owner_id="owner-a")
        project_b = await _make_project(db, owner_id="owner-a")
        dataset_b = await _make_dataset(db, project_id=project_b.id, owner_id="owner-a")

        resp = await training_service.submit_hpo_training_job(
            db,
            _hpo_request(project_id=project_a.id, dataset_id=dataset_b.id),
            user=None,
        )
        assert resp.status is JobStatus.PENDING
        assert len(spy_apply["hpo"]) == 1

    async def test_authenticated_owner_can_reuse_own_dataset_cross_project(
        self, db: AsyncSession, spy_apply
    ) -> None:
        """Not just an auth-off no-op: an authenticated caller who owns both
        the target project and the dataset (from a different project) is
        allowed too."""
        project_a = await _make_project(db, owner_id=ALICE.id)
        project_b = await _make_project(db, owner_id=ALICE.id)
        dataset_b = await _make_dataset(db, project_id=project_b.id, owner_id=ALICE.id)

        resp = await training_service.submit_manual_training_job(
            db,
            ManualTrainingRequest(project_id=project_a.id, dataset_id=dataset_b.id),
            user=ALICE,
        )
        assert resp.status is JobStatus.PENDING


# =============================================================================
# Orphaned datasets (project_id IS NULL)
# =============================================================================


class TestOrphanedDatasetReuse:
    async def test_orphaned_dataset_matching_owner_succeeds(
        self, db: AsyncSession, spy_apply
    ) -> None:
        project_a = await _make_project(db, owner_id=ALICE.id)
        orphan = await _make_dataset(db, project_id=None, owner_id=ALICE.id)

        resp = await training_service.submit_manual_training_job(
            db,
            ManualTrainingRequest(project_id=project_a.id, dataset_id=orphan.id),
            user=ALICE,
        )
        assert resp.status is JobStatus.PENDING

    async def test_orphaned_dataset_hpo_matching_owner_succeeds(
        self, db: AsyncSession, spy_apply
    ) -> None:
        project_a = await _make_project(db, owner_id=ALICE.id)
        orphan = await _make_dataset(db, project_id=None, owner_id=ALICE.id)

        resp = await training_service.submit_hpo_training_job(
            db,
            _hpo_request(project_id=project_a.id, dataset_id=orphan.id),
            user=ALICE,
        )
        assert resp.status is JobStatus.PENDING

    async def test_orphaned_dataset_user_none_succeeds(
        self, db: AsyncSession, spy_apply
    ) -> None:
        project_a = await _make_project(db, owner_id=None)
        orphan = await _make_dataset(db, project_id=None, owner_id=None)

        resp = await training_service.submit_manual_training_job(
            db,
            ManualTrainingRequest(project_id=project_a.id, dataset_id=orphan.id),
            user=None,
        )
        assert resp.status is JobStatus.PENDING


# =============================================================================
# user=None allows all (existing direct callers keep working)
# =============================================================================


class TestUserNoneAllowsAll:
    async def test_default_omitted_user_kwarg_still_works(
        self, db: AsyncSession, spy_apply
    ) -> None:
        """Existing direct callers never pass `user` at all — the new
        trailing kwarg must default to None and behave identically."""
        project = await _make_project(db, owner_id="owner-a")
        dataset = await _make_dataset(db, project_id=project.id, owner_id="owner-a")

        resp = await training_service.submit_manual_training_job(
            db, ManualTrainingRequest(project_id=project.id, dataset_id=dataset.id)
        )
        assert resp.status is JobStatus.PENDING

    async def test_user_none_allows_dataset_owned_by_someone_else(
        self, db: AsyncSession, spy_apply
    ) -> None:
        project = await _make_project(db, owner_id="owner-a")
        dataset = await _make_dataset(db, project_id=project.id, owner_id="owner-b")

        resp = await training_service.submit_manual_training_job(
            db,
            ManualTrainingRequest(project_id=project.id, dataset_id=dataset.id),
            user=None,
        )
        assert resp.status is JobStatus.PENDING


# =============================================================================
# Authenticated, fail-closed cases -> 403
# =============================================================================


class TestAuthenticatedForbidden:
    async def test_non_owner_gets_403(self, db: AsyncSession, spy_apply) -> None:
        project = await _make_project(db, owner_id=ALICE.id)
        dataset = await _make_dataset(db, project_id=project.id, owner_id=BOB.id)

        before = await _dataset_owned_row_count(db)
        with pytest.raises(HTTPException) as excinfo:
            await training_service.submit_manual_training_job(
                db,
                ManualTrainingRequest(project_id=project.id, dataset_id=dataset.id),
                user=ALICE,
            )
        assert excinfo.value.status_code == 403
        assert len(spy_apply["training"]) == 0
        assert await _dataset_owned_row_count(db) == before

    async def test_non_owner_gets_403_on_hpo(self, db: AsyncSession, spy_apply) -> None:
        project = await _make_project(db, owner_id=ALICE.id)
        dataset = await _make_dataset(db, project_id=project.id, owner_id=BOB.id)

        with pytest.raises(HTTPException) as excinfo:
            await training_service.submit_hpo_training_job(
                db,
                _hpo_request(project_id=project.id, dataset_id=dataset.id),
                user=ALICE,
            )
        assert excinfo.value.status_code == 403
        assert len(spy_apply["hpo"]) == 0

    async def test_null_owner_dataset_plus_authenticated_user_gets_403(
        self, db: AsyncSession, spy_apply
    ) -> None:
        """`Dataset.owner_id IS NULL` belongs to nobody, not to everybody —
        fails closed for an authenticated caller even though the dataset
        would be reachable under `user=None`."""
        project = await _make_project(db, owner_id=ALICE.id)
        legacy_dataset = await _make_dataset(db, project_id=project.id, owner_id=None)

        with pytest.raises(HTTPException) as excinfo:
            await training_service.submit_manual_training_job(
                db,
                ManualTrainingRequest(project_id=project.id, dataset_id=legacy_dataset.id),
                user=ALICE,
            )
        assert excinfo.value.status_code == 403
        assert len(spy_apply["training"]) == 0

    async def test_missing_dataset_still_404s_with_identical_message(
        self, db: AsyncSession, spy_apply
    ) -> None:
        project = await _make_project(db, owner_id="owner-a")
        missing_id = uuid4()

        with pytest.raises(HTTPException) as excinfo:
            await training_service.submit_manual_training_job(
                db,
                ManualTrainingRequest(project_id=project.id, dataset_id=missing_id),
                user=None,
            )
        assert excinfo.value.status_code == 404
        assert excinfo.value.detail == f"Dataset {missing_id} not found"


async def _dataset_owned_row_count(db: AsyncSession) -> int:
    from sqlalchemy import func, select

    from api.models.training_job import TrainingJob

    return int((await db.execute(select(func.count()).select_from(TrainingJob))).scalar_one())


# =============================================================================
# Unchanged gates: task_type mismatch (400), not-ready dataset (409)
# =============================================================================


class TestUnchangedGatesStillApply:
    async def test_task_type_mismatch_still_400s(self, db: AsyncSession, spy_apply) -> None:
        project = await _make_project(db, owner_id="owner-a", task_type=TaskType.QA)
        dataset = await _make_dataset(
            db,
            project_id=project.id,
            owner_id="owner-a",
            task_type=TaskType.CLASSIFICATION,
        )

        with pytest.raises(HTTPException) as excinfo:
            await training_service.submit_manual_training_job(
                db,
                ManualTrainingRequest(project_id=project.id, dataset_id=dataset.id),
                user=None,
            )
        assert excinfo.value.status_code == 400
        assert len(spy_apply["training"]) == 0

    async def test_task_type_mismatch_still_400s_on_hpo(
        self, db: AsyncSession, spy_apply
    ) -> None:
        project = await _make_project(db, owner_id="owner-a", task_type=TaskType.QA)
        dataset = await _make_dataset(
            db,
            project_id=project.id,
            owner_id="owner-a",
            task_type=TaskType.CLASSIFICATION,
        )

        with pytest.raises(HTTPException) as excinfo:
            await training_service.submit_hpo_training_job(
                db,
                _hpo_request(project_id=project.id, dataset_id=dataset.id),
                user=None,
            )
        assert excinfo.value.status_code == 400
        assert len(spy_apply["hpo"]) == 0

    async def test_storage_uri_none_still_409s(self, db: AsyncSession, spy_apply) -> None:
        project = await _make_project(db, owner_id="owner-a")
        dataset = await _make_dataset(
            db, project_id=project.id, owner_id="owner-a", storage_uri=None
        )

        with pytest.raises(HTTPException) as excinfo:
            await training_service.submit_manual_training_job(
                db,
                ManualTrainingRequest(project_id=project.id, dataset_id=dataset.id),
                user=None,
            )
        assert excinfo.value.status_code == 409
        assert len(spy_apply["training"]) == 0

    async def test_storage_uri_none_still_409s_on_hpo(
        self, db: AsyncSession, spy_apply
    ) -> None:
        project = await _make_project(db, owner_id="owner-a")
        dataset = await _make_dataset(
            db, project_id=project.id, owner_id="owner-a", storage_uri=None
        )

        with pytest.raises(HTTPException) as excinfo:
            await training_service.submit_hpo_training_job(
                db,
                _hpo_request(project_id=project.id, dataset_id=dataset.id),
                user=None,
            )
        assert excinfo.value.status_code == 409
        assert len(spy_apply["hpo"]) == 0
