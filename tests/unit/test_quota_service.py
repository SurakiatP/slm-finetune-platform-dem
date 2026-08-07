"""Unit tests for `api/services/quota.py`'s concurrency gate.

All DB-backed tests run against an in-memory aiosqlite engine (no Postgres,
no Docker). `Dataset`/`Project` use `sqlalchemy.dialects.postgresql.JSONB`,
which sqlite doesn't understand natively — the `@compiles` shim below is the
same pattern established in `tests/unit/test_dataset_status.py`.

Covers the five acceptance criteria from the task:
  1. Per-actor SDG cap (2) trips on the 3rd in-flight dataset for that actor;
     a different actor is unaffected. Rejection carries a `Retry-After`
     header.
  2. The GPU bucket is genuinely shared across TrainingJob / EvaluationRun /
     ModelArtifact.export_status — one in-flight training already at the
     per-actor cap (1) blocks BOTH an evaluation submit and an export submit
     for that same actor.
  3. `actor_id=None` never trips the per-actor cap (no `Project.owner_id` to
     join against) but still trips the global cap.
  4. completed/failed/cancelled rows are not counted.
  5. `api.services.quota` imports cleanly in a subprocess with `jwt` blocked
     at the meta-path, mirroring `tests/unit/test_worker_import_surface.py`'s
     technique — this module must never pull in `api.core.auth` /
     `api.services.ownership` even transitively.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from api.core.config import get_settings
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.evaluation_run import EvaluationRun
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.enums import DatasetSource, JobStatus, TaskType, TrainingMode
from api.services import quota


# ---- KNOWN GOTCHA shim: JSONB is Postgres-only, sqlite needs a stand-in ----


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


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


async def _make_project(db: AsyncSession, *, owner_id: str | None) -> Project:
    project = Project(id=uuid4(), name="proj", task_type=TaskType.QA, owner_id=owner_id)
    db.add(project)
    await db.flush()
    return project


async def _make_dataset(db: AsyncSession, *, project: Project, status: JobStatus) -> Dataset:
    dataset = Dataset(
        id=uuid4(),
        project_id=project.id,
        name="ds",
        task_type=TaskType.QA,
        source=DatasetSource.SDG,
        status=status,
        num_samples=0,
    )
    db.add(dataset)
    await db.flush()
    return dataset


async def _make_training_job(db: AsyncSession, *, project: Project, dataset: Dataset, status: JobStatus) -> TrainingJob:
    job = TrainingJob(
        id=uuid4(),
        project_id=project.id,
        dataset_id=dataset.id,
        mode=TrainingMode.MANUAL,
        status=status,
        base_model="base",
        config_json={},
    )
    db.add(job)
    await db.flush()
    return job


async def _make_model_artifact(
    db: AsyncSession, *, training_job: TrainingJob, export_status: JobStatus | None
) -> ModelArtifact:
    artifact = ModelArtifact(
        id=uuid4(),
        training_job_id=training_job.id,
        name="artifact",
        base_model="base",
        export_status=export_status,
    )
    db.add(artifact)
    await db.flush()
    return artifact


async def _make_evaluation_run(
    db: AsyncSession, *, model_artifact: ModelArtifact, dataset: Dataset, status: JobStatus
) -> EvaluationRun:
    run = EvaluationRun(
        id=uuid4(),
        model_artifact_id=model_artifact.id,
        dataset_id=dataset.id,
        status=status,
    )
    db.add(run)
    await db.flush()
    return run


# =============================================================================
# 1. Per-actor SDG cap (quota_max_sdg_jobs_per_actor default = 2)
# =============================================================================


class TestSdgPerActorCap:
    async def test_third_in_flight_dataset_for_actor_raises_429_with_retry_after(
        self, db: AsyncSession
    ) -> None:
        settings = get_settings()
        actor_a = "actor-a"
        project_a = await _make_project(db, owner_id=actor_a)
        await _make_dataset(db, project=project_a, status=JobStatus.PENDING)
        await _make_dataset(db, project=project_a, status=JobStatus.RUNNING)

        with pytest.raises(HTTPException) as excinfo:
            await quota.assert_can_submit(db, bucket=quota.Bucket.SDG, actor_id=actor_a)

        assert excinfo.value.status_code == 429
        assert excinfo.value.headers is not None
        assert excinfo.value.headers["Retry-After"] == str(settings.quota_retry_after_seconds)

    async def test_a_different_actor_is_unaffected(self, db: AsyncSession) -> None:
        actor_a = "actor-a"
        actor_b = "actor-b"
        project_a = await _make_project(db, owner_id=actor_a)
        await _make_dataset(db, project=project_a, status=JobStatus.PENDING)
        await _make_dataset(db, project=project_a, status=JobStatus.RUNNING)

        # Actor B has no in-flight datasets of their own; the global count
        # (2) is still below the global cap (8), so this must succeed.
        await quota.assert_can_submit(db, bucket=quota.Bucket.SDG, actor_id=actor_b)


# =============================================================================
# 2. GPU bucket shared across TrainingJob / EvaluationRun / ModelArtifact
#    (quota_max_gpu_jobs_per_actor default = 1)
# =============================================================================


class TestGpuBucketIsShared:
    async def test_one_running_training_blocks_evaluation_and_export_submits(
        self, db: AsyncSession
    ) -> None:
        actor_a = "actor-a"
        project_a = await _make_project(db, owner_id=actor_a)
        dataset = await _make_dataset(db, project=project_a, status=JobStatus.COMPLETED)
        await _make_training_job(db, project=project_a, dataset=dataset, status=JobStatus.RUNNING)

        # An eval submit for actor A must 429 — the training run alone
        # already saturates the per-actor GPU cap (1).
        with pytest.raises(HTTPException) as eval_excinfo:
            await quota.assert_can_submit(db, bucket=quota.Bucket.GPU, actor_id=actor_a)
        assert eval_excinfo.value.status_code == 429

        # An export submit for actor A must 429 too — same shared bucket.
        with pytest.raises(HTTPException) as export_excinfo:
            await quota.assert_can_submit(db, bucket=quota.Bucket.GPU, actor_id=actor_a)
        assert export_excinfo.value.status_code == 429

    async def test_running_evaluation_alone_saturates_the_shared_bucket(
        self, db: AsyncSession
    ) -> None:
        actor_a = "actor-a"
        project_a = await _make_project(db, owner_id=actor_a)
        dataset = await _make_dataset(db, project=project_a, status=JobStatus.COMPLETED)
        training_job = await _make_training_job(
            db, project=project_a, dataset=dataset, status=JobStatus.COMPLETED
        )
        artifact = await _make_model_artifact(db, training_job=training_job, export_status=None)
        await _make_evaluation_run(
            db, model_artifact=artifact, dataset=dataset, status=JobStatus.RUNNING
        )

        with pytest.raises(HTTPException):
            await quota.assert_can_submit(db, bucket=quota.Bucket.GPU, actor_id=actor_a)

    async def test_running_export_alone_saturates_the_shared_bucket(
        self, db: AsyncSession
    ) -> None:
        actor_a = "actor-a"
        project_a = await _make_project(db, owner_id=actor_a)
        dataset = await _make_dataset(db, project=project_a, status=JobStatus.COMPLETED)
        training_job = await _make_training_job(
            db, project=project_a, dataset=dataset, status=JobStatus.COMPLETED
        )
        await _make_model_artifact(
            db, training_job=training_job, export_status=JobStatus.PENDING
        )

        with pytest.raises(HTTPException):
            await quota.assert_can_submit(db, bucket=quota.Bucket.GPU, actor_id=actor_a)


# =============================================================================
# 3. Anonymous callers: never trip per-actor, but do trip global
# =============================================================================


class TestAnonymousCallers:
    async def test_anonymous_never_trips_per_actor_cap(self, db: AsyncSession) -> None:
        # Actor A alone is already over their own per-actor SDG cap (2).
        actor_a = "actor-a"
        project_a = await _make_project(db, owner_id=actor_a)
        await _make_dataset(db, project=project_a, status=JobStatus.PENDING)
        await _make_dataset(db, project=project_a, status=JobStatus.RUNNING)

        # An anonymous submit only checks the global cap (8), which is not
        # yet reached, so it must succeed even though a per-actor cap
        # somewhere in the table is already blown.
        await quota.assert_can_submit(db, bucket=quota.Bucket.SDG, actor_id=None)

    async def test_anonymous_does_trip_the_global_cap(self, db: AsyncSession) -> None:
        settings = get_settings()
        # No owner at all — anonymous-created project, mirrors phase-1
        # compatibility mode where owner_id can be NULL.
        project = await _make_project(db, owner_id=None)
        for _ in range(settings.quota_max_sdg_jobs_global):
            await _make_dataset(db, project=project, status=JobStatus.RUNNING)

        with pytest.raises(HTTPException) as excinfo:
            await quota.assert_can_submit(db, bucket=quota.Bucket.SDG, actor_id=None)
        assert excinfo.value.status_code == 429


# =============================================================================
# 4. Terminal statuses are never counted
# =============================================================================


class TestTerminalStatusesIgnored:
    async def test_completed_failed_cancelled_datasets_dont_count(
        self, db: AsyncSession
    ) -> None:
        actor_a = "actor-a"
        project_a = await _make_project(db, owner_id=actor_a)
        await _make_dataset(db, project=project_a, status=JobStatus.COMPLETED)
        await _make_dataset(db, project=project_a, status=JobStatus.FAILED)
        await _make_dataset(db, project=project_a, status=JobStatus.CANCELLED)

        # None of the three in-flight-looking rows above are PENDING/RUNNING,
        # so this submit (which would be the 1st in-flight one) must succeed.
        await quota.assert_can_submit(db, bucket=quota.Bucket.SDG, actor_id=actor_a)

    async def test_completed_failed_cancelled_gpu_rows_dont_count(
        self, db: AsyncSession
    ) -> None:
        actor_a = "actor-a"
        project_a = await _make_project(db, owner_id=actor_a)
        dataset = await _make_dataset(db, project=project_a, status=JobStatus.COMPLETED)
        await _make_training_job(
            db, project=project_a, dataset=dataset, status=JobStatus.COMPLETED
        )
        training_job_2 = await _make_training_job(
            db, project=project_a, dataset=dataset, status=JobStatus.FAILED
        )
        artifact = await _make_model_artifact(
            db, training_job=training_job_2, export_status=JobStatus.CANCELLED
        )
        await _make_evaluation_run(
            db, model_artifact=artifact, dataset=dataset, status=JobStatus.FAILED
        )

        # Nothing above is PENDING/RUNNING, so the per-actor GPU cap (1) is
        # not yet touched and this submit must succeed.
        await quota.assert_can_submit(db, bucket=quota.Bucket.GPU, actor_id=actor_a)


# =============================================================================
# 5. Import-surface safety: no PyJWT dependency, even transitively
# =============================================================================

_REPO_ROOT = Path(__file__).resolve().parents[2]

_BLOCK_JWT_AND_IMPORT = """
import sys, importlib.abc


class _NoPyJWT(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name == "jwt" or name.startswith("jwt."):
            raise ModuleNotFoundError(
                "No module named 'jwt' (simulating the worker image)"
            )
        return None


sys.meta_path.insert(0, _NoPyJWT())
import {module}
print("IMPORTED")
"""


def _import_without_pyjwt(module: str) -> subprocess.CompletedProcess[str]:
    import os

    return subprocess.run(
        [sys.executable, "-c", _BLOCK_JWT_AND_IMPORT.format(module=module)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        env={
            **dict(os.environ),
            "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test_unused",
        },
    )


def test_quota_module_is_importable_without_pyjwt() -> None:
    """`api.services.quota` must never pull in `api.core.auth` /
    `api.services.ownership` (both `import jwt` transitively) — the GPU
    worker image ships no PyJWT, and this gate is meant to be callable from
    worker-adjacent code paths without crash-looping the fleet."""
    result = _import_without_pyjwt("api.services.quota")
    assert "IMPORTED" in result.stdout, (
        f"api.services.quota cannot be imported in the worker image.\n"
        f"stderr:\n{result.stderr}"
    )
