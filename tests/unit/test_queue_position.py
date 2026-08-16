"""Unit tests for `api/services/queue_position.py` (T8).

Covers:
  1. `compute_queue` core: all three legs (training / export / eval) seeded
     together, FIFO ordering by the documented per-leg timestamp.
  2. Exclusions: terminal statuses (CANCELLED/COMPLETED/FAILED) don't count,
     SDG `Dataset` rows are out of scope entirely, empty DB -> {}.
  3. A project with both a RUNNING row and a PENDING row is "processing"
     (RUNNING wins over PENDING on the same project).
  4. Owner grouping: `owner_queue_position` resets per owner; the
     `owner_id IS NULL` anonymous bucket is one shared group.
  5. `project_queue_info` — right entry, `None` for a project not queued.
  6. Wiring into the four detail getters (`projects_service.get_project`,
     `trainings_service.get_training`, `model_service.get_model`,
     `evaluation_service.get_evaluation`): populated trio while in-flight,
     all-`None` trio once terminal, and never populated on list endpoints.

Same in-memory aiosqlite pattern as `tests/unit/test_gpu_quota_guards.py`
(`Dataset`/`Project`/... use `postgresql.JSONB`, which needs the `@compiles`
shim below to run against sqlite). No `torch` import anywhere in this file.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from api.core import request_context
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.evaluation_run import EvaluationRun
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.enums import DatasetSource, JobStatus, TaskType, TrainingMode
from api.services import evaluation_service, model_service, projects_service, trainings_service
from api.services.queue_position import ProjectQueueInfo, compute_queue, project_queue_info


# ---- KNOWN GOTCHA shim: JSONB is Postgres-only, sqlite needs a stand-in ----


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


BASE_MODEL = "unsloth/Llama-3.2-3B-Instruct-bnb-4bit"
T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _ts(offset_seconds: int) -> datetime:
    """Deterministic, strictly-ordered timestamps independent of DB clock
    granularity (sqlite server_default `CURRENT_TIMESTAMP` is second-grained,
    so tests that need FIFO ordering must set timestamps explicitly)."""
    return T0 + timedelta(seconds=offset_seconds)


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


# ---- row factories ----------------------------------------------------------


async def _make_project(db: AsyncSession, *, owner_id: str | None = None, name: str = "proj") -> Project:
    project = Project(id=uuid4(), name=name, task_type=TaskType.QA, owner_id=owner_id)
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


async def _make_pending_seed_dataset(db: AsyncSession, *, project: Project) -> Dataset:
    """An SDG dataset row still generating (PENDING). Used only to prove
    queue_position.py never counts the SDG leg — see the module docstring's
    'SDG is deliberately excluded' note."""
    dataset = Dataset(
        id=uuid4(),
        project_id=project.id,
        name="seed-ds",
        task_type=project.task_type,
        source=DatasetSource.SDG,
        status=JobStatus.PENDING,
        num_samples=0,
    )
    db.add(dataset)
    await db.flush()
    return dataset


async def _make_training_job(
    db: AsyncSession,
    *,
    project: Project,
    dataset: Dataset,
    status: JobStatus,
    created_at: datetime | None = None,
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
    if created_at is not None:
        # TimestampMixin columns are server_default=func.now(); explicitly
        # assigning before the INSERT overrides that default so tests can
        # control FIFO ordering deterministically.
        job.created_at = created_at
        job.updated_at = created_at
    db.add(job)
    await db.flush()
    return job


async def _make_artifact(
    db: AsyncSession,
    *,
    training_job: TrainingJob,
    export_status: JobStatus | None = None,
    updated_at: datetime | None = None,
    name: str = "artifact",
) -> ModelArtifact:
    artifact = ModelArtifact(
        id=uuid4(),
        training_job_id=training_job.id,
        name=name,
        base_model=BASE_MODEL,
        lora_adapter_uri="s3://models/adapters/x",
        ollama_model_tag=f"{name}-{uuid4().hex[:8]}:latest",
        export_status=export_status,
        export_celery_task_id="job-in-flight" if export_status else None,
    )
    if updated_at is not None:
        artifact.created_at = updated_at
        # export leg orders by `updated_at`, not `created_at` — see
        # queue_position.py's module docstring. Set explicitly since a
        # server_default `onupdate` only fires on a later UPDATE, not on the
        # initial INSERT if the column is already provided.
        artifact.updated_at = updated_at
    db.add(artifact)
    await db.flush()
    return artifact


async def _make_evaluation(
    db: AsyncSession,
    *,
    artifact: ModelArtifact,
    dataset: Dataset,
    status: JobStatus,
    created_at: datetime | None = None,
) -> EvaluationRun:
    ev = EvaluationRun(
        id=uuid4(),
        model_artifact_id=artifact.id,
        dataset_id=dataset.id,
        status=status,
    )
    if created_at is not None:
        ev.created_at = created_at
        ev.updated_at = created_at
    db.add(ev)
    await db.flush()
    return ev


# =============================================================================
# 1. compute_queue core — all three legs seeded together, FIFO ordering
# =============================================================================


class TestComputeQueueAllThreeLegs:
    async def test_fifo_order_across_training_export_eval_legs(self, db: AsyncSession) -> None:
        # Project A: RUNNING training job -> processing, not queued.
        project_a = await _make_project(db, name="A")
        dataset_a = await _make_ready_dataset(db, project=project_a)
        await _make_training_job(
            db, project=project_a, dataset=dataset_a, status=JobStatus.RUNNING, created_at=_ts(0)
        )

        # Project B: PENDING training job, earliest queued timestamp -> #1.
        project_b = await _make_project(db, name="B")
        dataset_b = await _make_ready_dataset(db, project=project_b)
        await _make_training_job(
            db, project=project_b, dataset=dataset_b, status=JobStatus.PENDING, created_at=_ts(10)
        )

        # Project C: export leg — ModelArtifact.export_status=PENDING via a
        # COMPLETED TrainingJob chain, ranked by ModelArtifact.updated_at -> #2.
        project_c = await _make_project(db, name="C")
        dataset_c = await _make_ready_dataset(db, project=project_c)
        training_c = await _make_training_job(
            db, project=project_c, dataset=dataset_c, status=JobStatus.COMPLETED, created_at=_ts(1)
        )
        await _make_artifact(
            db, training_job=training_c, export_status=JobStatus.PENDING, updated_at=_ts(20)
        )

        # Project D: eval leg — EvaluationRun PENDING via
        # ModelArtifact->TrainingJob chain, ranked by EvaluationRun.created_at -> #3.
        project_d = await _make_project(db, name="D")
        dataset_d = await _make_ready_dataset(db, project=project_d)
        training_d = await _make_training_job(
            db, project=project_d, dataset=dataset_d, status=JobStatus.COMPLETED, created_at=_ts(1)
        )
        artifact_d = await _make_artifact(db, training_job=training_d, name="artifact-d")
        await _make_evaluation(
            db, artifact=artifact_d, dataset=dataset_d, status=JobStatus.PENDING, created_at=_ts(30)
        )

        queue = await compute_queue(db)

        assert queue[project_a.id] == ProjectQueueInfo(
            queue_state="processing", queue_position=None, owner_queue_position=None
        )
        assert queue[project_b.id].queue_state == "queued"
        assert queue[project_c.id].queue_state == "queued"
        assert queue[project_d.id].queue_state == "queued"
        assert queue[project_b.id].queue_position == 1
        assert queue[project_c.id].queue_position == 2
        assert queue[project_d.id].queue_position == 3
        # Anonymous group (owner_id=None for all) -> owner position == global.
        assert queue[project_b.id].owner_queue_position == 1
        assert queue[project_c.id].owner_queue_position == 2
        assert queue[project_d.id].owner_queue_position == 3


# =============================================================================
# 2. Exclusions
# =============================================================================


class TestExclusions:
    async def test_terminal_statuses_dont_count(self, db: AsyncSession) -> None:
        project_cancelled = await _make_project(db, name="cancelled")
        ds1 = await _make_ready_dataset(db, project=project_cancelled)
        await _make_training_job(db, project=project_cancelled, dataset=ds1, status=JobStatus.CANCELLED)

        project_completed = await _make_project(db, name="completed")
        ds2 = await _make_ready_dataset(db, project=project_completed)
        await _make_training_job(db, project=project_completed, dataset=ds2, status=JobStatus.COMPLETED)

        project_failed = await _make_project(db, name="failed")
        ds3 = await _make_ready_dataset(db, project=project_failed)
        await _make_training_job(db, project=project_failed, dataset=ds3, status=JobStatus.FAILED)

        queue = await compute_queue(db)

        assert project_cancelled.id not in queue
        assert project_completed.id not in queue
        assert project_failed.id not in queue

    async def test_sdg_only_project_is_absent(self, db: AsyncSession) -> None:
        """A project with only a PENDING SDG `Dataset` row (no training/export/
        eval row at all) must not appear in the GPU queue — SDG is out of
        scope for this module (see queue_position.py's module docstring)."""
        project = await _make_project(db, name="sdg-only")
        await _make_pending_seed_dataset(db, project=project)

        queue = await compute_queue(db)

        assert project.id not in queue

    async def test_empty_db_returns_empty_dict(self, db: AsyncSession) -> None:
        assert await compute_queue(db) == {}


# =============================================================================
# 3. Mixed project: RUNNING + PENDING -> processing wins
# =============================================================================


class TestMixedProjectRunningWinsOverPending:
    async def test_running_plus_pending_is_processing(self, db: AsyncSession) -> None:
        project = await _make_project(db, name="mixed")
        dataset = await _make_ready_dataset(db, project=project)
        # An earlier training run currently RUNNING...
        await _make_training_job(
            db, project=project, dataset=dataset, status=JobStatus.RUNNING, created_at=_ts(0)
        )
        # ...and a second training job already queued behind it (e.g. HPO
        # child submitted while the first job still holds the GPU).
        await _make_training_job(
            db, project=project, dataset=dataset, status=JobStatus.PENDING, created_at=_ts(5)
        )

        queue = await compute_queue(db)

        assert queue[project.id] == ProjectQueueInfo(
            queue_state="processing", queue_position=None, owner_queue_position=None
        )


# =============================================================================
# 4. Owner grouping
# =============================================================================


class TestOwnerGrouping:
    async def test_owner_position_resets_per_owner(self, db: AsyncSession) -> None:
        # Interleaved by timestamp: X, Y, X -> global 1,2,3; X:1,2; Y:1.
        project_x1 = await _make_project(db, owner_id="owner-x", name="x1")
        ds_x1 = await _make_ready_dataset(db, project=project_x1)
        await _make_training_job(
            db, project=project_x1, dataset=ds_x1, status=JobStatus.PENDING, created_at=_ts(0)
        )

        project_y1 = await _make_project(db, owner_id="owner-y", name="y1")
        ds_y1 = await _make_ready_dataset(db, project=project_y1)
        await _make_training_job(
            db, project=project_y1, dataset=ds_y1, status=JobStatus.PENDING, created_at=_ts(10)
        )

        project_x2 = await _make_project(db, owner_id="owner-x", name="x2")
        ds_x2 = await _make_ready_dataset(db, project=project_x2)
        await _make_training_job(
            db, project=project_x2, dataset=ds_x2, status=JobStatus.PENDING, created_at=_ts(20)
        )

        queue = await compute_queue(db)

        assert queue[project_x1.id].queue_position == 1
        assert queue[project_y1.id].queue_position == 2
        assert queue[project_x2.id].queue_position == 3

        assert queue[project_x1.id].owner_queue_position == 1
        assert queue[project_x2.id].owner_queue_position == 2
        assert queue[project_y1.id].owner_queue_position == 1

    async def test_all_none_owners_share_one_anonymous_group(self, db: AsyncSession) -> None:
        """Per `quota.py`'s precedent, `owner_id IS NULL` is one shared group,
        not 'each anonymous project has no group' — so under today's
        `AUTH_REQUIRED=false` default, owner_queue_position == queue_position
        for every queued project."""
        projects = []
        for i in range(3):
            project = await _make_project(db, owner_id=None, name=f"anon{i}")
            dataset = await _make_ready_dataset(db, project=project)
            await _make_training_job(
                db, project=project, dataset=dataset, status=JobStatus.PENDING, created_at=_ts(i * 10)
            )
            projects.append(project)

        queue = await compute_queue(db)

        for position, project in enumerate(projects, start=1):
            assert queue[project.id].queue_position == position
            assert queue[project.id].owner_queue_position == position
            assert queue[project.id].owner_queue_position == queue[project.id].queue_position


# =============================================================================
# 5. project_queue_info
# =============================================================================


class TestProjectQueueInfo:
    async def test_returns_the_right_entry(self, db: AsyncSession) -> None:
        project = await _make_project(db, name="solo")
        dataset = await _make_ready_dataset(db, project=project)
        await _make_training_job(
            db, project=project, dataset=dataset, status=JobStatus.PENDING, created_at=_ts(0)
        )

        info = await project_queue_info(db, project.id)

        assert info is not None
        assert info.queue_state == "queued"
        assert info.queue_position == 1
        assert info.owner_queue_position == 1

    async def test_returns_none_for_a_project_not_in_queue(self, db: AsyncSession) -> None:
        # A queued project exists in the DB, but we ask about an unrelated one.
        queued_project = await _make_project(db, name="queued")
        dataset = await _make_ready_dataset(db, project=queued_project)
        await _make_training_job(
            db, project=queued_project, dataset=dataset, status=JobStatus.PENDING, created_at=_ts(0)
        )

        idle_project = await _make_project(db, name="idle")

        info = await project_queue_info(db, idle_project.id)

        assert info is None


# =============================================================================
# 6. Wiring into the four detail getters
# =============================================================================


class TestWiringIntoDetailGetters:
    async def test_get_project_populates_trio_when_queued(self, db: AsyncSession) -> None:
        project = await _make_project(db, name="wired-project")
        dataset = await _make_ready_dataset(db, project=project)
        await _make_training_job(
            db, project=project, dataset=dataset, status=JobStatus.PENDING, created_at=_ts(0)
        )

        response = await projects_service.get_project(db, project.id, user=None)

        assert response.queue_state == "queued"
        assert response.queue_position == 1
        assert response.owner_queue_position == 1

    async def test_get_project_all_none_when_nothing_in_flight(self, db: AsyncSession) -> None:
        project = await _make_project(db, name="idle-project")
        dataset = await _make_ready_dataset(db, project=project)
        await _make_training_job(
            db, project=project, dataset=dataset, status=JobStatus.COMPLETED
        )

        response = await projects_service.get_project(db, project.id, user=None)

        assert response.queue_state is None
        assert response.queue_position is None
        assert response.owner_queue_position is None

    async def test_get_training_populates_trio_when_pending(self, db: AsyncSession) -> None:
        project = await _make_project(db, name="wired-training")
        dataset = await _make_ready_dataset(db, project=project)
        job = await _make_training_job(
            db, project=project, dataset=dataset, status=JobStatus.PENDING, created_at=_ts(0)
        )

        response = await trainings_service.get_training(db, job.id, user=None)

        assert response.queue_state == "queued"
        assert response.queue_position == 1
        assert response.owner_queue_position == 1

    async def test_get_training_all_none_when_terminal(self, db: AsyncSession) -> None:
        project = await _make_project(db, name="terminal-training")
        dataset = await _make_ready_dataset(db, project=project)
        job = await _make_training_job(
            db, project=project, dataset=dataset, status=JobStatus.COMPLETED
        )

        response = await trainings_service.get_training(db, job.id, user=None)

        assert response.queue_state is None
        assert response.queue_position is None
        assert response.owner_queue_position is None

    async def test_get_model_populates_trio_when_export_pending(self, db: AsyncSession) -> None:
        project = await _make_project(db, name="wired-model")
        dataset = await _make_ready_dataset(db, project=project)
        training_job = await _make_training_job(
            db, project=project, dataset=dataset, status=JobStatus.COMPLETED
        )
        artifact = await _make_artifact(
            db, training_job=training_job, export_status=JobStatus.PENDING, updated_at=_ts(0)
        )

        response = await model_service.get_model(db, artifact.id, user=None)

        assert response.queue_state == "queued"
        assert response.queue_position == 1
        assert response.owner_queue_position == 1

    async def test_get_model_all_none_when_no_export_in_flight(self, db: AsyncSession) -> None:
        project = await _make_project(db, name="idle-model")
        dataset = await _make_ready_dataset(db, project=project)
        training_job = await _make_training_job(
            db, project=project, dataset=dataset, status=JobStatus.COMPLETED
        )
        artifact = await _make_artifact(db, training_job=training_job, export_status=None)

        response = await model_service.get_model(db, artifact.id, user=None)

        assert response.queue_state is None
        assert response.queue_position is None
        assert response.owner_queue_position is None

    async def test_get_evaluation_populates_trio_when_pending(self, db: AsyncSession) -> None:
        project = await _make_project(db, name="wired-eval")
        dataset = await _make_ready_dataset(db, project=project)
        training_job = await _make_training_job(
            db, project=project, dataset=dataset, status=JobStatus.COMPLETED
        )
        artifact = await _make_artifact(db, training_job=training_job)
        ev = await _make_evaluation(
            db, artifact=artifact, dataset=dataset, status=JobStatus.PENDING, created_at=_ts(0)
        )

        response = await evaluation_service.get_evaluation(db, ev.id, user=None)

        assert response.queue_state == "queued"
        assert response.queue_position == 1
        assert response.owner_queue_position == 1

    async def test_get_evaluation_all_none_when_terminal(self, db: AsyncSession) -> None:
        project = await _make_project(db, name="terminal-eval")
        dataset = await _make_ready_dataset(db, project=project)
        training_job = await _make_training_job(
            db, project=project, dataset=dataset, status=JobStatus.COMPLETED
        )
        artifact = await _make_artifact(db, training_job=training_job)
        ev = await _make_evaluation(
            db, artifact=artifact, dataset=dataset, status=JobStatus.COMPLETED
        )

        response = await evaluation_service.get_evaluation(db, ev.id, user=None)

        assert response.queue_state is None
        assert response.queue_position is None
        assert response.owner_queue_position is None

    async def test_list_endpoints_never_populate_queue_fields(self, db: AsyncSession) -> None:
        """Even when a project is genuinely queued, list_* responses must
        leave queue_state/queue_position/owner_queue_position untouched
        (None) — populating them would require an N+1 lookup per row, which
        queue_position.py's wiring deliberately avoids."""
        project = await _make_project(db, name="queued-but-listed")
        dataset = await _make_ready_dataset(db, project=project)
        job = await _make_training_job(
            db, project=project, dataset=dataset, status=JobStatus.PENDING, created_at=_ts(0)
        )

        # Sanity: this project really is queued (position 1) via the detail path.
        info = await project_queue_info(db, project.id)
        assert info is not None and info.queue_state == "queued"

        projects_page = await projects_service.list_projects(db, limit=10, offset=0)
        project_item = next(p for p in projects_page.items if p.id == project.id)
        assert project_item.queue_state is None
        assert project_item.queue_position is None
        assert project_item.owner_queue_position is None

        trainings_page = await trainings_service.list_trainings(
            db, project_id=None, status_filter=None, limit=10, offset=0
        )
        training_item = next(t for t in trainings_page.items if t.id == job.id)
        assert training_item.queue_state is None
        assert training_item.queue_position is None
        assert training_item.owner_queue_position is None
