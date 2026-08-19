"""Unit tests for W1-T5: the training-create contract.

Covers three independent layers:

  1. Schema-level (no DB) — `training_name` must be ollama-tag-safe
     (`^[a-z0-9][a-z0-9._-]{0,62}$`), rejecting uppercase / spaces / a
     leading dash with a 422-shaped `pydantic.ValidationError`. `auto_export`
     / `auto_evaluate` default to `False` and are settable on both
     `ManualTrainingRequest` and `HPOTrainingRequest` (they live on the
     shared `_TrainingRequestBase`).

  2. Service-level (in-memory aiosqlite) — `training_name` uniqueness is
     enforced within the *owner* scope (join TrainingJob -> Project on
     `owner_id`), not just within one project: a duplicate submitted for a
     different project owned by the same actor still 409s. Two different
     owners (including the `owner_id IS NULL` bucket vs. a named owner) may
     reuse the same name freely. `auto_export`/`auto_evaluate` from the
     request are persisted onto the inserted `TrainingJob` row.

  3. Response-level (no DB) — `TrainingResponse` exposes `auto_export`,
     `auto_evaluate`, `auto_pipeline` and round-trips a populated
     `auto_pipeline` dict via `from_attributes=True` off a real (committed)
     `TrainingJob` row, exercising the same path `trainings_service.get_
     training`/`list_trainings` use.

Everything here runs against an in-memory aiosqlite engine only — no
Postgres, no Docker, no GPU, no real Celery broker. Follows the sqlite
JSONB `@compiles` shim pattern from `tests/unit/test_dataset_status.py` /
`tests/unit/test_gpu_quota_guards.py` (Project/Dataset/TrainingJob use
`sqlalchemy.dialects.postgresql.JSONB`, which sqlite has no native type
for).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from api.core import request_context
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.enums import DatasetSource, JobStatus, TaskType, TrainingMode
from api.schemas.trainings import TrainingResponse
from api.schemas.training import (
    HPOConfig,
    HPOFloatRange,
    HPOSearchSpace,
    HPOTrainingRequest,
    ManualTrainingRequest,
)
from api.services import training_service


# ---- KNOWN GOTCHA shim: JSONB is Postgres-only, sqlite needs a stand-in ----


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


BASE_MODEL = "unsloth/Llama-3.2-3B-Instruct-bnb-4bit"


# =============================================================================
# 1. Schema-level (no DB needed)
# =============================================================================


class TestTrainingNamePattern:
    @pytest.mark.parametrize(
        "bad_name",
        [
            "QA-Policy-V1",  # uppercase
            "qa policy v1",  # spaces
            "-qa-policy-v1",  # leading dash
        ],
    )
    def test_rejects_bad_pattern(self, bad_name: str) -> None:
        with pytest.raises(ValidationError) as excinfo:
            ManualTrainingRequest(
                project_id=uuid4(), dataset_id=uuid4(), training_name=bad_name
            )
        errors = excinfo.value.errors()
        assert any(e["loc"] == ("training_name",) for e in errors)
        # The message must explain the allowed character set, not just say
        # "invalid" — that's the whole point of a custom validator here.
        msg = next(e["msg"] for e in errors if e["loc"] == ("training_name",))
        assert "lowercase" in msg

    @pytest.mark.parametrize("good_name", ["qa-policy-v1", "run.1_a", "a", "0-name"])
    def test_accepts_ollama_tag_safe_names(self, good_name: str) -> None:
        req = ManualTrainingRequest(
            project_id=uuid4(), dataset_id=uuid4(), training_name=good_name
        )
        assert req.training_name == good_name

    def test_none_is_allowed(self) -> None:
        req = ManualTrainingRequest(project_id=uuid4(), dataset_id=uuid4())
        assert req.training_name is None

    def test_max_length_63_enforced(self) -> None:
        too_long = "a" * 64
        with pytest.raises(ValidationError):
            ManualTrainingRequest(
                project_id=uuid4(), dataset_id=uuid4(), training_name=too_long
            )
        ok = "a" * 63
        req = ManualTrainingRequest(
            project_id=uuid4(), dataset_id=uuid4(), training_name=ok
        )
        assert req.training_name == ok

    def test_hpo_request_gets_the_same_validator(self) -> None:
        """`training_name` lives on the shared `_TrainingRequestBase`, so the
        HPO request must reject the same bad names as manual."""
        with pytest.raises(ValidationError):
            HPOTrainingRequest(
                project_id=uuid4(),
                dataset_id=uuid4(),
                training_name="Bad Name",
                hpo_config=HPOConfig(
                    search_space=HPOSearchSpace(
                        learning_rate=HPOFloatRange(low=1e-5, high=5e-4, log=True)
                    ),
                ),
            )


class TestAutoExportAutoEvaluateFlags:
    def test_default_to_false_on_manual(self) -> None:
        req = ManualTrainingRequest(project_id=uuid4(), dataset_id=uuid4())
        assert req.auto_export is False
        assert req.auto_evaluate is False

    def test_settable_true_on_manual(self) -> None:
        req = ManualTrainingRequest(
            project_id=uuid4(), dataset_id=uuid4(), auto_export=True, auto_evaluate=True
        )
        assert req.auto_export is True
        assert req.auto_evaluate is True

    def test_default_to_false_on_hpo(self) -> None:
        req = HPOTrainingRequest(
            project_id=uuid4(),
            dataset_id=uuid4(),
            hpo_config=HPOConfig(
                search_space=HPOSearchSpace(
                    learning_rate=HPOFloatRange(low=1e-5, high=5e-4, log=True)
                ),
            ),
        )
        assert req.auto_export is False
        assert req.auto_evaluate is False


# =============================================================================
# 2. Service-level (in-memory aiosqlite)
# =============================================================================


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
    between tests — matches `test_gpu_quota_guards.py`'s fixture."""
    yield
    request_context.clear()


@pytest.fixture
def spy_apply(monkeypatch):
    """Patch `apply_async` on both training Celery tasks so no real broker
    is touched, and record calls so 'nothing enqueued on the 409 path' is
    provable rather than assumed."""
    calls: dict[str, list[dict]] = {"training": [], "hpo": []}
    counter = {"n": 0}

    class _Result:
        def __init__(self, job_id: str) -> None:
            self.id = job_id

    def _make(name):
        def _apply_async(**kwargs):
            calls[name].append(kwargs)
            counter["n"] += 1
            # celery_task_id is UNIQUE on TrainingJob — a fixed id would
            # collide across the multiple successful submits several tests
            # here make in one run.
            return _Result(f"job-{name}-{counter['n']}")

        return _apply_async

    import workers.tasks.hpo_training as hpo_task
    import workers.tasks.training as training_task

    monkeypatch.setattr(training_task.train_manual, "apply_async", _make("training"))
    monkeypatch.setattr(hpo_task.train_hpo, "apply_async", _make("hpo"))
    return calls


async def _make_project(db: AsyncSession, *, owner_id: str | None) -> Project:
    project = Project(id=uuid4(), name="proj", task_type=TaskType.QA, owner_id=owner_id)
    db.add(project)
    await db.flush()
    return project


async def _make_ready_dataset(db: AsyncSession, *, project: Project) -> Dataset:
    dataset = Dataset(
        id=uuid4(),
        project_id=project.id,
        name="ds",
        task_type=project.task_type,
        source=DatasetSource.SDG,
        status=JobStatus.COMPLETED,
        num_samples=5,
        storage_uri="s3://datasets/ds.jsonl",
    )
    db.add(dataset)
    await db.flush()
    return dataset


async def _row_count(db: AsyncSession, model) -> int:
    return int((await db.execute(select(func.count()).select_from(model))).scalar_one())


def _hpo_request(*, project_id, dataset_id, training_name=None, auto_export=False, auto_evaluate=False):
    return HPOTrainingRequest(
        project_id=project_id,
        dataset_id=dataset_id,
        training_name=training_name,
        auto_export=auto_export,
        auto_evaluate=auto_evaluate,
        hpo_config=HPOConfig(
            search_space=HPOSearchSpace(
                learning_rate=HPOFloatRange(low=1e-5, high=5e-4, log=True)
            ),
        ),
    )


class TestDuplicateTrainingNameSameOwner:
    async def test_manual_then_manual_409s(self, db: AsyncSession, spy_apply) -> None:
        project = await _make_project(db, owner_id="owner-a")
        dataset = await _make_ready_dataset(db, project=project)

        first = await training_service.submit_manual_training_job(
            db,
            ManualTrainingRequest(
                project_id=project.id, dataset_id=dataset.id, training_name="qa-policy-v1"
            ),
        )
        assert first.status is JobStatus.PENDING

        before = await _row_count(db, TrainingJob)
        with pytest.raises(HTTPException) as excinfo:
            await training_service.submit_manual_training_job(
                db,
                ManualTrainingRequest(
                    project_id=project.id, dataset_id=dataset.id, training_name="qa-policy-v1"
                ),
            )

        assert excinfo.value.status_code == 409
        assert excinfo.value.detail == "training_name 'qa-policy-v1' already exists"
        assert await _row_count(db, TrainingJob) == before, "no row on the 409 path"
        assert first.job_id is not None
        assert len(spy_apply["training"]) == 1, "the duplicate must never reach apply_async"

    async def test_hpo_then_hpo_409s(self, db: AsyncSession, spy_apply) -> None:
        project = await _make_project(db, owner_id="owner-a")
        dataset = await _make_ready_dataset(db, project=project)

        await training_service.submit_hpo_training_job(
            db, _hpo_request(project_id=project.id, dataset_id=dataset.id, training_name="search-1")
        )

        with pytest.raises(HTTPException) as excinfo:
            await training_service.submit_hpo_training_job(
                db,
                _hpo_request(
                    project_id=project.id, dataset_id=dataset.id, training_name="search-1"
                ),
            )
        assert excinfo.value.status_code == 409
        assert excinfo.value.detail == "training_name 'search-1' already exists"
        assert len(spy_apply["hpo"]) == 1

    async def test_manual_then_hpo_same_name_409s(self, db: AsyncSession, spy_apply) -> None:
        """The uniqueness check is cross-mode: manual and HPO share the same
        `training_name` namespace within one owner."""
        project = await _make_project(db, owner_id="owner-a")
        dataset = await _make_ready_dataset(db, project=project)

        await training_service.submit_manual_training_job(
            db,
            ManualTrainingRequest(
                project_id=project.id, dataset_id=dataset.id, training_name="shared-name"
            ),
        )

        with pytest.raises(HTTPException) as excinfo:
            await training_service.submit_hpo_training_job(
                db,
                _hpo_request(
                    project_id=project.id, dataset_id=dataset.id, training_name="shared-name"
                ),
            )
        assert excinfo.value.status_code == 409

    async def test_duplicate_across_two_projects_same_owner_409s(
        self, db: AsyncSession, spy_apply
    ) -> None:
        """Uniqueness is scoped to the OWNER, not the project — two projects
        owned by the same actor must not be able to reuse a name either."""
        owner = "owner-a"
        project1 = await _make_project(db, owner_id=owner)
        dataset1 = await _make_ready_dataset(db, project=project1)
        project2 = await _make_project(db, owner_id=owner)
        dataset2 = await _make_ready_dataset(db, project=project2)

        await training_service.submit_manual_training_job(
            db,
            ManualTrainingRequest(
                project_id=project1.id, dataset_id=dataset1.id, training_name="cross-project"
            ),
        )

        with pytest.raises(HTTPException) as excinfo:
            await training_service.submit_manual_training_job(
                db,
                ManualTrainingRequest(
                    project_id=project2.id,
                    dataset_id=dataset2.id,
                    training_name="cross-project",
                ),
            )
        assert excinfo.value.status_code == 409

    async def test_null_owner_projects_share_one_scope(self, db: AsyncSession, spy_apply) -> None:
        """Two different projects with `owner_id IS NULL` (phase-1 /
        auth-disabled rows) must still collide with each other — they share
        one scope rather than each being its own island."""
        project1 = await _make_project(db, owner_id=None)
        dataset1 = await _make_ready_dataset(db, project=project1)
        project2 = await _make_project(db, owner_id=None)
        dataset2 = await _make_ready_dataset(db, project=project2)

        await training_service.submit_manual_training_job(
            db,
            ManualTrainingRequest(
                project_id=project1.id, dataset_id=dataset1.id, training_name="null-owner-name"
            ),
        )

        with pytest.raises(HTTPException) as excinfo:
            await training_service.submit_manual_training_job(
                db,
                ManualTrainingRequest(
                    project_id=project2.id,
                    dataset_id=dataset2.id,
                    training_name="null-owner-name",
                ),
            )
        assert excinfo.value.status_code == 409


class TestSameNameDifferentOwnersOK:
    async def test_two_named_owners_can_reuse_a_name(self, db: AsyncSession, spy_apply) -> None:
        project_a = await _make_project(db, owner_id="owner-a")
        dataset_a = await _make_ready_dataset(db, project=project_a)
        project_b = await _make_project(db, owner_id="owner-b")
        dataset_b = await _make_ready_dataset(db, project=project_b)

        resp_a = await training_service.submit_manual_training_job(
            db,
            ManualTrainingRequest(
                project_id=project_a.id, dataset_id=dataset_a.id, training_name="shared-across-owners"
            ),
        )
        resp_b = await training_service.submit_manual_training_job(
            db,
            ManualTrainingRequest(
                project_id=project_b.id, dataset_id=dataset_b.id, training_name="shared-across-owners"
            ),
        )

        assert resp_a.training_id != resp_b.training_id
        assert await _row_count(db, TrainingJob) == 2
        assert len(spy_apply["training"]) == 2

    async def test_named_owner_vs_null_owner_can_reuse_a_name(
        self, db: AsyncSession, spy_apply
    ) -> None:
        project_named = await _make_project(db, owner_id="owner-a")
        dataset_named = await _make_ready_dataset(db, project=project_named)
        project_null = await _make_project(db, owner_id=None)
        dataset_null = await _make_ready_dataset(db, project=project_null)

        await training_service.submit_manual_training_job(
            db,
            ManualTrainingRequest(
                project_id=project_named.id,
                dataset_id=dataset_named.id,
                training_name="cross-scope-name",
            ),
        )
        resp = await training_service.submit_manual_training_job(
            db,
            ManualTrainingRequest(
                project_id=project_null.id,
                dataset_id=dataset_null.id,
                training_name="cross-scope-name",
            ),
        )
        assert resp.status is JobStatus.PENDING
        assert await _row_count(db, TrainingJob) == 2


class TestNoNamePassesThroughFreely:
    async def test_multiple_untitled_submits_never_collide(
        self, db: AsyncSession, spy_apply
    ) -> None:
        project = await _make_project(db, owner_id="owner-a")
        dataset = await _make_ready_dataset(db, project=project)

        await training_service.submit_manual_training_job(
            db, ManualTrainingRequest(project_id=project.id, dataset_id=dataset.id)
        )
        await training_service.submit_manual_training_job(
            db, ManualTrainingRequest(project_id=project.id, dataset_id=dataset.id)
        )
        assert await _row_count(db, TrainingJob) == 2


class TestAutoFlagsPersisted:
    async def test_manual_persists_auto_flags(self, db: AsyncSession, spy_apply) -> None:
        project = await _make_project(db, owner_id="owner-a")
        dataset = await _make_ready_dataset(db, project=project)

        resp = await training_service.submit_manual_training_job(
            db,
            ManualTrainingRequest(
                project_id=project.id,
                dataset_id=dataset.id,
                auto_export=True,
                auto_evaluate=True,
            ),
        )

        got = await db.get(TrainingJob, resp.training_id)
        assert got is not None
        assert got.auto_export is True
        assert got.auto_evaluate is True

    async def test_manual_defaults_persist_false(self, db: AsyncSession, spy_apply) -> None:
        project = await _make_project(db, owner_id="owner-a")
        dataset = await _make_ready_dataset(db, project=project)

        resp = await training_service.submit_manual_training_job(
            db, ManualTrainingRequest(project_id=project.id, dataset_id=dataset.id)
        )

        got = await db.get(TrainingJob, resp.training_id)
        assert got is not None
        assert got.auto_export is False
        assert got.auto_evaluate is False

    async def test_hpo_persists_auto_flags(self, db: AsyncSession, spy_apply) -> None:
        project = await _make_project(db, owner_id="owner-a")
        dataset = await _make_ready_dataset(db, project=project)

        resp = await training_service.submit_hpo_training_job(
            db,
            _hpo_request(
                project_id=project.id,
                dataset_id=dataset.id,
                auto_export=True,
                auto_evaluate=False,
            ),
        )

        got = await db.get(TrainingJob, resp.training_id)
        assert got is not None
        assert got.auto_export is True
        assert got.auto_evaluate is False


# =============================================================================
# 3. Response-level (no DB round-trip needed for validation, but we build
#    real committed rows to mirror trainings_service.get_training exactly)
# =============================================================================


class TestTrainingResponseExposesAutoPipelineFields:
    async def test_defaults_round_trip(self, db: AsyncSession) -> None:
        project = await _make_project(db, owner_id="owner-a")
        dataset = await _make_ready_dataset(db, project=project)
        job = TrainingJob(
            id=uuid4(),
            project_id=project.id,
            dataset_id=dataset.id,
            mode=TrainingMode.MANUAL,
            status=JobStatus.PENDING,
            base_model=BASE_MODEL,
            config_json={},
        )
        db.add(job)
        await db.flush()
        await db.refresh(job)

        resp = TrainingResponse.model_validate(job)
        assert resp.auto_export is False
        assert resp.auto_evaluate is False
        assert resp.auto_pipeline is None

    async def test_populated_auto_pipeline_round_trips(self, db: AsyncSession) -> None:
        project = await _make_project(db, owner_id="owner-a")
        dataset = await _make_ready_dataset(db, project=project)
        pipeline_state = {
            "export": {"status": "completed", "artifact_id": str(uuid4()), "error": None},
            "evaluate": {
                "status": "running",
                "evaluation_id": None,
                "skip_reason": None,
                "error": None,
            },
        }
        job = TrainingJob(
            id=uuid4(),
            project_id=project.id,
            dataset_id=dataset.id,
            mode=TrainingMode.MANUAL,
            status=JobStatus.RUNNING,
            base_model=BASE_MODEL,
            config_json={},
            auto_export=True,
            auto_evaluate=True,
            auto_pipeline=pipeline_state,
        )
        db.add(job)
        await db.flush()
        await db.refresh(job)

        resp = TrainingResponse.model_validate(job)
        assert resp.auto_export is True
        assert resp.auto_evaluate is True
        assert resp.auto_pipeline == pipeline_state

    async def test_get_training_service_exposes_the_fields(self, db: AsyncSession) -> None:
        """Not just `TrainingResponse.model_validate` in isolation — the
        actual `trainings_service.get_training`/`list_trainings` call sites
        the API routes through. No code changes were needed there: both
        already do `TrainingResponse.model_validate(job)` /
        `from_attributes=True` off the ORM row, which picks up the new
        columns for free once they exist on `TrainingJob`."""
        from api.services import trainings_service

        project = await _make_project(db, owner_id="owner-a")
        dataset = await _make_ready_dataset(db, project=project)
        job = TrainingJob(
            id=uuid4(),
            project_id=project.id,
            dataset_id=dataset.id,
            mode=TrainingMode.MANUAL,
            status=JobStatus.COMPLETED,
            base_model=BASE_MODEL,
            config_json={},
            auto_export=True,
            auto_evaluate=False,
            auto_pipeline={"export": {"status": "completed"}},
        )
        db.add(job)
        await db.commit()

        got = await trainings_service.get_training(db, job.id, user=None)
        assert got.auto_export is True
        assert got.auto_evaluate is False
        assert got.auto_pipeline == {"export": {"status": "completed"}}

        page = await trainings_service.list_trainings(
            db, project_id=None, status_filter=None, limit=10, offset=0, user=None
        )
        listed = next(item for item in page.items if item.id == job.id)
        assert listed.auto_export is True
        assert listed.auto_pipeline == {"export": {"status": "completed"}}
