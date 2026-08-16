"""T7: unit tests for the RTX 3060 VRAM safety guard (step 3b) added to
`api/services/training_service.py::submit_manual_training_job`.

The guard rejects a manual submit whose `manual_config.per_device_train_batch_size`
would OOM at the requested `max_seq_length` for the chosen `base_model`, using
`_max_safe_batch_for_3060` (`_MAX_SAFE_BATCH_3060` lookup table keyed by
params-billions bucket + seq-length bucket). It runs AFTER the base_model
allowlist check (step 3) and BEFORE the GPU quota gate (step 4) and the
`TrainingJob` row insert (step 5) — a rejected submit must leave no row behind
and must never reach `apply_async`.

Fixture stack copied from `tests/unit/test_gpu_quota_guards.py:74-134` (same
in-memory aiosqlite engine, the `@compiles(JSONB, "sqlite")` shim `Dataset`/
`Project` need to run against sqlite, the `request_context` contextvar
autouse-clear, and `spy_apply` which patches `apply_async` on the Celery task
objects so "nothing was enqueued" is provable rather than assumed).
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
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.enums import DatasetSource, JobStatus, TaskType
from api.schemas.training import ManualTrainingConfig, ManualTrainingRequest
from api.services import training_service


# ---- KNOWN GOTCHA shim: JSONB is Postgres-only, sqlite needs a stand-in ----


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


# 3.21B params — falls in the "<=3.5B" bucket of `_MAX_SAFE_BATCH_3060`, whose
# seq<=2048 row caps `per_device_train_batch_size` at 2. Also allowlisted, so
# it's present in both `_SUPPORTED_MODEL_IDS` and `_PARAMS_BY_MODEL_ID`.
BASE_MODEL = "unsloth/Llama-3.2-3B-Instruct-bnb-4bit"
UNKNOWN_BASE_MODEL = "unsloth/does-not-exist-4bit"


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
    """Patches `apply_async` on the manual-training Celery task and records
    every call, so 'nothing was enqueued' on the guard-rejection path is
    provable rather than assumed."""
    calls: list[dict] = []

    class _Result:
        id = "job-newly-created"

    def _apply_async(**kwargs):
        calls.append(kwargs)
        return _Result()

    import workers.tasks.training as training_task

    monkeypatch.setattr(training_task.train_manual, "apply_async", _apply_async)
    return calls


# ---- row factories ----------------------------------------------------------


async def _make_project(db: AsyncSession) -> Project:
    project = Project(id=uuid4(), name="proj", task_type=TaskType.QA, owner_id=None)
    db.add(project)
    await db.flush()
    return project


async def _make_ready_dataset(db: AsyncSession, *, project: Project) -> Dataset:
    """A dataset that has finished generation (storage_uri + rows set) so it
    passes the "is this dataset ready" checks earlier in submit."""
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


# =============================================================================
# (a) Over-ceiling batch -> 422, no row, no enqueue
# =============================================================================


class TestOverCeilingBatchRejected:
    async def test_over_ceiling_batch_raises_422_with_no_side_effects(
        self, db: AsyncSession, spy_apply
    ) -> None:
        project = await _make_project(db)
        dataset = await _make_ready_dataset(db, project=project)
        before = await _row_count(db, TrainingJob)

        request = ManualTrainingRequest(
            project_id=project.id,
            dataset_id=dataset.id,
            base_model=BASE_MODEL,
            manual_config=ManualTrainingConfig(
                per_device_train_batch_size=4,  # ceiling at seq=2048 is 2
                max_seq_length=2048,
            ),
        )

        with pytest.raises(HTTPException) as excinfo:
            await training_service.submit_manual_training_job(db, request)

        assert excinfo.value.status_code == 422
        detail = excinfo.value.detail
        assert "per_device_train_batch_size" in detail
        assert "max_seq_length" in detail
        assert await _row_count(db, TrainingJob) == before, (
            "a guard-rejected submit must not leave a TrainingJob row behind"
        )
        assert spy_apply == [], "a guard-rejected submit must never reach apply_async"


# =============================================================================
# (b) At-ceiling batch passes the guard
# =============================================================================


class TestAtCeilingBatchPassesGuard:
    async def test_at_ceiling_batch_does_not_trip_the_guard(
        self, db: AsyncSession, spy_apply
    ) -> None:
        project = await _make_project(db)
        dataset = await _make_ready_dataset(db, project=project)

        request = ManualTrainingRequest(
            project_id=project.id,
            dataset_id=dataset.id,
            base_model=BASE_MODEL,
            manual_config=ManualTrainingConfig(
                per_device_train_batch_size=2,  # exactly the ceiling at seq=2048
                max_seq_length=2048,
            ),
        )

        # Should sail past the VRAM guard (and quota gate, and land a row +
        # an enqueue) — proving the guard's boundary is `>`, not `>=`.
        response = await training_service.submit_manual_training_job(db, request)

        assert response.status == JobStatus.PENDING
        assert len(spy_apply) == 1, "an accepted submit must reach apply_async exactly once"


# =============================================================================
# (c) Unknown base_model: allowlist (step 3) rejects before the guard (3b) runs
# =============================================================================


class TestUnknownBaseModelSkipsGuard:
    async def test_unknown_base_model_fails_allowlist_not_the_vram_guard(
        self, db: AsyncSession, spy_apply
    ) -> None:
        project = await _make_project(db)
        dataset = await _make_ready_dataset(db, project=project)
        before = await _row_count(db, TrainingJob)

        # per_device_train_batch_size=16 would trip the VRAM guard for every
        # allowlisted model's ceiling table — if the 422 below turned out to
        # mention per_device_train_batch_size, that would mean the guard ran
        # before (or instead of) the allowlist check; it must not.
        request = ManualTrainingRequest(
            project_id=project.id,
            dataset_id=dataset.id,
            base_model=UNKNOWN_BASE_MODEL,
            manual_config=ManualTrainingConfig(per_device_train_batch_size=16),
        )

        with pytest.raises(HTTPException) as excinfo:
            await training_service.submit_manual_training_job(db, request)

        assert excinfo.value.status_code == 422
        detail = excinfo.value.detail
        assert "not in the supported list" in detail
        assert "per_device_train_batch_size" not in detail, (
            "an unknown base_model must be rejected by the allowlist check, "
            "never by the VRAM guard (which never even ran)"
        )
        assert await _row_count(db, TrainingJob) == before
        assert spy_apply == []
