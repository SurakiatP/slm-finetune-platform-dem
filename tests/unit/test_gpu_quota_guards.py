"""GPU-bucket quota gate (T15) at the four submit call sites.

`api/services/quota.py` (owned by another agent, not edited here) provides
`assert_can_submit(db, bucket=Bucket.GPU, actor_id=...)`. This file proves it
was actually wired into all four places that spend GPU time:

  - `training_service.submit_manual_training_job`
  - `training_service.submit_hpo_training_job`
  - `evaluation_service.submit_evaluation_job`
  - `model_service.submit_export_job`

Covers the task's three acceptance criteria:
  1. Each of the four raises 429 with a `Retry-After` header once the actor
     already holds a running GPU job (per-actor cap is 1 by default).
  2. THE important one — `submit_export_job` on an artifact with an
     in-flight export still returns 409, not 429, even when the actor is
     simultaneously over the GPU quota. The in-flight-export 409 guard runs
     before the quota gate on purpose (see the comment at its call site in
     `model_service.py`), so the two never race for which fires first.
  3. On every 429 path: no row is inserted (or, for export, the artifact's
     export_status/export_celery_task_id are left untouched) and no
     `apply_async` is called — a rejected submit must not spend GPU time or
     leave a dangling PENDING row nobody will ever pick up.

All DB-backed tests run against an in-memory aiosqlite engine, same pattern
as `tests/unit/test_quota_service.py` and `tests/unit/test_dataset_status.py`
(`Dataset`/`Project`/... use `postgresql.JSONB`, which needs the `@compiles`
shim below to run against sqlite).

Ownership checks (`ownership.assert_model_access` / `assert_dataset_access`)
are exercised for real with `user=None` — per `ownership.py`'s own contract
that's a complete no-op, so it doesn't get in the way of testing the quota
gate. The actor the quota gate cares about is set independently via
`request_context`'s contextvar (`current_user_id()`), exactly as
`training_service` / `evaluation_service` / `model_service` already resolve
it for their audit-log calls — that's the "actor" this file saturates.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from api.core import request_context
from api.core.config import get_settings
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.evaluation_run import EvaluationRun
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.artifacts import ModelExportRequest
from api.schemas.enums import ArtifactFormat, DatasetSource, JobStatus, TaskType, TrainingMode
from api.schemas.evaluations import EvaluationCreate
from api.schemas.training import (
    HPOConfig,
    HPOFloatRange,
    HPOSearchSpace,
    HPOTrainingRequest,
    ManualTrainingRequest,
)
from api.services import evaluation_service, model_service, training_service


# ---- KNOWN GOTCHA shim: JSONB is Postgres-only, sqlite needs a stand-in ----


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


ACTOR = "actor-over-quota"
BASE_MODEL = "unsloth/Llama-3.2-3B-Instruct-bnb-4bit"


# =============================================================================
# Fixtures
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
    """`current_user_id()` is a contextvar — reset it around every test so
    one test's actor binding can never leak into the next."""
    yield
    request_context.clear()


@pytest.fixture
def spy_apply(monkeypatch):
    """Patches `apply_async` on all four Celery tasks these services enqueue
    and records every call, keyed by task name, so 'nothing was enqueued' on
    the 429 path is provable rather than assumed."""
    calls: dict[str, list[dict]] = {"training": [], "hpo": [], "evaluation": [], "export": []}

    class _Result:
        id = "job-newly-created"

    def _make(name):
        def _apply_async(**kwargs):
            calls[name].append(kwargs)
            return _Result()

        return _apply_async

    import workers.tasks.evaluation as evaluation_task
    import workers.tasks.hpo_training as hpo_task
    import workers.tasks.model_export as export_task
    import workers.tasks.training as training_task

    monkeypatch.setattr(training_task.train_manual, "apply_async", _make("training"))
    monkeypatch.setattr(hpo_task.train_hpo, "apply_async", _make("hpo"))
    monkeypatch.setattr(evaluation_task.run_evaluation, "apply_async", _make("evaluation"))
    monkeypatch.setattr(export_task.export_model, "apply_async", _make("export"))
    return calls


# ---- row factories ----------------------------------------------------------


async def _make_project(db: AsyncSession, *, owner_id: str | None) -> Project:
    project = Project(id=uuid4(), name="proj", task_type=TaskType.QA, owner_id=owner_id)
    db.add(project)
    await db.flush()
    return project


async def _make_ready_dataset(db: AsyncSession, *, project: Project) -> Dataset:
    """A dataset that has finished generation (storage_uri + rows set) so it
    passes the "is this dataset ready" checks in training/evaluation submit."""
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


async def _make_training_job(
    db: AsyncSession, *, project: Project, dataset: Dataset, status: JobStatus
) -> TrainingJob:
    job = TrainingJob(
        id=uuid4(),
        project_id=project.id,
        dataset_id=dataset.id,
        mode=TrainingMode.MANUAL,
        status=status,
        base_model=BASE_MODEL,
        config_json={},
    )
    db.add(job)
    await db.flush()
    return job


async def _make_ready_artifact(
    db: AsyncSession,
    *,
    training_job: TrainingJob,
    export_status: JobStatus | None = None,
) -> ModelArtifact:
    """A LoRA-adapter-complete, Ollama-registered artifact — passes both
    `submit_export_job`'s lora_adapter_uri check and
    `submit_evaluation_job`'s ollama_model_tag check."""
    artifact = ModelArtifact(
        id=uuid4(),
        training_job_id=training_job.id,
        name="artifact",
        base_model=BASE_MODEL,
        lora_adapter_uri="s3://models/adapters/x",
        ollama_model_tag="artifact:latest",
        export_status=export_status,
        export_celery_task_id="job-already-running" if export_status else None,
    )
    db.add(artifact)
    await db.flush()
    return artifact


async def _saturate_gpu_bucket_for(db: AsyncSession, *, project: Project, dataset: Dataset) -> None:
    """Give `project`'s owner one RUNNING TrainingJob — enough by itself to
    blow the default per-actor GPU cap (quota_max_gpu_jobs_per_actor=1)."""
    await _make_training_job(db, project=project, dataset=dataset, status=JobStatus.RUNNING)


def _hpo_request(*, project_id, dataset_id) -> HPOTrainingRequest:
    return HPOTrainingRequest(
        project_id=project_id,
        dataset_id=dataset_id,
        hpo_config=HPOConfig(
            search_space=HPOSearchSpace(
                learning_rate=HPOFloatRange(low=1e-5, high=5e-4, log=True)
            ),
        ),
    )


async def _row_count(db: AsyncSession, model) -> int:
    return int((await db.execute(select(func.count()).select_from(model))).scalar_one())


# =============================================================================
# 1 + 3. Each of the four submit functions: 429 + Retry-After, no row, no enqueue
# =============================================================================


class TestManualTrainingOverQuota:
    async def test_429_with_retry_after(self, db: AsyncSession, spy_apply) -> None:
        settings = get_settings()
        project = await _make_project(db, owner_id=ACTOR)
        dataset = await _make_ready_dataset(db, project=project)
        await _saturate_gpu_bucket_for(db, project=project, dataset=dataset)
        request_context.set_user_id(ACTOR)

        before = await _row_count(db, TrainingJob)

        with pytest.raises(HTTPException) as excinfo:
            await training_service.submit_manual_training_job(
                db,
                ManualTrainingRequest(project_id=project.id, dataset_id=dataset.id),
            )

        assert excinfo.value.status_code == 429
        assert excinfo.value.headers is not None
        assert excinfo.value.headers["Retry-After"] == str(settings.quota_retry_after_seconds)
        assert await _row_count(db, TrainingJob) == before, "no TrainingJob row on the 429 path"
        assert spy_apply["training"] == [], "a rejected submit must not spend GPU time"


class TestHpoTrainingOverQuota:
    async def test_429_with_retry_after(self, db: AsyncSession, spy_apply) -> None:
        settings = get_settings()
        project = await _make_project(db, owner_id=ACTOR)
        dataset = await _make_ready_dataset(db, project=project)
        await _saturate_gpu_bucket_for(db, project=project, dataset=dataset)
        request_context.set_user_id(ACTOR)

        before = await _row_count(db, TrainingJob)

        with pytest.raises(HTTPException) as excinfo:
            await training_service.submit_hpo_training_job(
                db, _hpo_request(project_id=project.id, dataset_id=dataset.id)
            )

        assert excinfo.value.status_code == 429
        assert excinfo.value.headers is not None
        assert excinfo.value.headers["Retry-After"] == str(settings.quota_retry_after_seconds)
        assert await _row_count(db, TrainingJob) == before, "no TrainingJob row on the 429 path"
        assert spy_apply["hpo"] == [], "a rejected submit must not spend GPU time"


class TestEvaluationOverQuota:
    async def test_429_with_retry_after(self, db: AsyncSession, spy_apply) -> None:
        settings = get_settings()
        project = await _make_project(db, owner_id=ACTOR)
        dataset = await _make_ready_dataset(db, project=project)
        training_job = await _make_training_job(
            db, project=project, dataset=dataset, status=JobStatus.COMPLETED
        )
        artifact = await _make_ready_artifact(db, training_job=training_job)
        await _saturate_gpu_bucket_for(db, project=project, dataset=dataset)
        request_context.set_user_id(ACTOR)

        before = await _row_count(db, EvaluationRun)

        with pytest.raises(HTTPException) as excinfo:
            await evaluation_service.submit_evaluation_job(
                db,
                EvaluationCreate(model_artifact_id=artifact.id, dataset_id=dataset.id),
                user=None,
            )

        assert excinfo.value.status_code == 429
        assert excinfo.value.headers is not None
        assert excinfo.value.headers["Retry-After"] == str(settings.quota_retry_after_seconds)
        assert await _row_count(db, EvaluationRun) == before, "no EvaluationRun row on the 429 path"
        assert spy_apply["evaluation"] == [], "a rejected submit must not spend GPU time"


class TestExportOverQuota:
    async def test_429_with_retry_after_when_no_export_in_flight(
        self, db: AsyncSession, spy_apply
    ) -> None:
        """Artifact has NO in-flight export (export_status=None) — the 409
        guard does not fire, so this submit reaches the quota gate and must
        429 for an over-quota actor."""
        settings = get_settings()
        project = await _make_project(db, owner_id=ACTOR)
        dataset = await _make_ready_dataset(db, project=project)
        training_job = await _make_training_job(
            db, project=project, dataset=dataset, status=JobStatus.COMPLETED
        )
        artifact = await _make_ready_artifact(db, training_job=training_job, export_status=None)
        await _saturate_gpu_bucket_for(db, project=project, dataset=dataset)
        request_context.set_user_id(ACTOR)

        with pytest.raises(HTTPException) as excinfo:
            await model_service.submit_export_job(
                db,
                model_id=artifact.id,
                request=ModelExportRequest(format=ArtifactFormat.GGUF),
                user=None,
            )

        assert excinfo.value.status_code == 429
        assert excinfo.value.headers is not None
        assert excinfo.value.headers["Retry-After"] == str(settings.quota_retry_after_seconds)
        # The 429 path must leave the artifact's export job-control columns
        # untouched — no PENDING flip, no celery task id stamped.
        assert artifact.export_status is None
        assert artifact.export_celery_task_id is None
        assert spy_apply["export"] == [], "a rejected submit must not spend GPU time"


# =============================================================================
# 2. THE ordering test: in-flight export -> 409, never 429, even over quota
# =============================================================================


class TestExportInFlightGuardOutranksQuota:
    """The single most important test in this task. The in-flight-export 409
    guard in `model_service.submit_export_job` must run BEFORE the quota
    gate — so an artifact that already has a running export gets the
    specific, actionable 409 (cancel it first) even when the calling actor
    is simultaneously over the GPU quota. If the ordering were ever
    reversed, this caller would get a generic 429 that doesn't tell them
    the real, fixable problem is their own in-flight export.
    """

    @pytest.mark.parametrize("in_flight_status", [JobStatus.PENDING, JobStatus.RUNNING])
    async def test_409_not_429_when_both_conditions_are_true(
        self, db: AsyncSession, spy_apply, in_flight_status: JobStatus
    ) -> None:
        project = await _make_project(db, owner_id=ACTOR)
        dataset = await _make_ready_dataset(db, project=project)
        training_job = await _make_training_job(
            db, project=project, dataset=dataset, status=JobStatus.COMPLETED
        )
        # Artifact already has an in-flight export.
        artifact = await _make_ready_artifact(
            db, training_job=training_job, export_status=in_flight_status
        )
        # AND the actor is simultaneously over the shared GPU quota.
        await _saturate_gpu_bucket_for(db, project=project, dataset=dataset)
        request_context.set_user_id(ACTOR)

        with pytest.raises(HTTPException) as excinfo:
            await model_service.submit_export_job(
                db,
                model_id=artifact.id,
                request=ModelExportRequest(format=ArtifactFormat.GGUF),
                user=None,
            )

        assert excinfo.value.status_code == 409, (
            "in-flight export must win over an over-quota actor — got "
            f"{excinfo.value.status_code} instead of 409"
        )
        assert "job-already-running" in excinfo.value.detail
        # The in-flight job's id must survive untouched (same regression the
        # in-flight guard itself protects against in
        # test_export_inflight_guard.py).
        assert artifact.export_celery_task_id == "job-already-running"
        assert artifact.export_status is in_flight_status
        assert spy_apply["export"] == [], "neither guard may let this reach apply_async"
