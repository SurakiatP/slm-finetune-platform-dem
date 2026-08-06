"""Unit tests for the four `POST .../cancel` job-control endpoints.

SDG is the job that actually spends money (OpenRouter calls, up to ~500
concurrent per loop), yet until this branch only *training* could be
cancelled. These tests pin the shared semantics all four now implement:

  • 404 when the resource doesn't exist
  • idempotent: cancelling an already-terminal job returns 200 with the
    existing status, performs no revoke, and mutates nothing
  • otherwise: revoke the Celery task, then flip status to CANCELLED
  • a broker failure during revoke must NOT block the status flip
  • the API never publishes a WS terminal frame — the revoked task's own
    `except` block already publishes `JobFailed`, and a second one would
    double-deliver

`DELETE /api/v1/trainings/{id}` (which `smart-model-tune` calls today at
`engineApi.ts:286`) must keep working and must share one implementation with
the new `POST` alias.

In-memory fakes only — no Postgres, no broker, no GPU.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from api.models.dataset import Dataset
from api.models.evaluation_run import EvaluationRun
from api.models.model_artifact import ModelArtifact
from api.models.training_job import TrainingJob
from api.schemas.enums import DatasetSource, JobStatus, TaskType
from api.services import (
    datasets_service,
    evaluation_service,
    model_service,
    trainings_service,
)
from api.services.job_control import TERMINAL_JOB_STATUSES, revoke_celery_task

_TERMINAL = [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]
_ACTIVE = [JobStatus.PENDING, JobStatus.RUNNING]


def _db(row: object | None) -> MagicMock:
    """A session that answers `get()` with `row` — but only for `row`'s own
    model.

    The type check matters: the cancel services now walk one or two hops to
    resolve the owning project for their audit row (ModelArtifact ->
    TrainingJob -> Project). A fake that returned `row` for *every* `get()`
    handed that walk a ModelArtifact where a TrainingJob belonged, which is
    not a shape production can produce. Returning None for other models keeps
    the fake honest; the audit row's `project_id` is nullable, so these
    cancel tests simply record it as unresolved.
    """
    db = MagicMock()

    async def _get(model, ident, *a, **kw):  # noqa: ANN001, ANN002, ANN003
        return row if row is not None and isinstance(row, model) else None

    db.get = AsyncMock(side_effect=_get)
    db.commit = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.fixture
def spy_revoke(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    """Capture task ids passed to revoke, across all four service modules."""
    calls: list[str | None] = []

    def _spy(task_id: str | None, *, context: str) -> None:
        calls.append(task_id)

    for module in (
        datasets_service,
        model_service,
        evaluation_service,
        trainings_service,
    ):
        monkeypatch.setattr(module, "revoke_celery_task", _spy)
    return calls


# ---- row builders -----------------------------------------------------------


def _dataset(status: JobStatus, *, task_id: str | None = "sdg-task", meta=None) -> Dataset:
    return Dataset(
        id=uuid4(),
        project_id=uuid4(),
        name="ds",
        source=DatasetSource.SDG,
        task_type=TaskType.QA,
        status=status,
        celery_task_id=task_id,
        generation_metadata=meta,
    )


def _artifact(export_status: JobStatus | None, *, task_id: str | None = "exp-task"):
    return ModelArtifact(
        id=uuid4(),
        training_job_id=uuid4(),
        name="m",
        base_model="unsloth/x",
        export_status=export_status,
        export_celery_task_id=task_id,
    )


def _evaluation(status: JobStatus) -> EvaluationRun:
    return EvaluationRun(
        id=uuid4(),
        model_artifact_id=uuid4(),
        dataset_id=uuid4(),
        status=status,
        celery_task_id="eval-task",
    )


def _training(status: JobStatus) -> TrainingJob:
    job = TrainingJob(id=uuid4(), status=status, celery_task_id="train-task")
    return job


# =============================================================================
# Shared semantics
# =============================================================================


class TestTerminalStatusSet:
    def test_terminal_set_is_exactly_the_three_finished_states(self) -> None:
        assert TERMINAL_JOB_STATUSES == frozenset(_TERMINAL)

    def test_active_states_are_not_terminal(self) -> None:
        for st in _ACTIVE:
            assert st not in TERMINAL_JOB_STATUSES


class TestRevokeHelper:
    def test_noop_on_missing_task_id(self) -> None:
        """A row that never got a Celery id must not blow up the cancel path."""
        revoke_celery_task(None, context="test")
        revoke_celery_task("", context="test")

    def test_broker_failure_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A Redis/broker hiccup must not leave a job permanently uncancellable."""
        import workers.celery_app as celery_module

        def boom(*a, **kw):
            raise RuntimeError("broker down")

        monkeypatch.setattr(celery_module.celery_app.control, "revoke", boom)

        revoke_celery_task("some-task", context="test")  # must not raise


# =============================================================================
# Per-resource behaviour
# =============================================================================


class TestCancelDataset:
    async def test_404_when_missing(self, spy_revoke) -> None:
        with pytest.raises(HTTPException) as exc:
            await datasets_service.cancel_dataset(_db(None), uuid4())
        assert exc.value.status_code == 404

    @pytest.mark.parametrize("status", _ACTIVE)
    async def test_flips_to_cancelled_and_revokes(self, spy_revoke, status) -> None:
        ds = _dataset(status)
        db = _db(ds)

        result = await datasets_service.cancel_dataset(db, ds.id)

        assert ds.status is JobStatus.CANCELLED
        assert result["status"] == "cancelled"
        assert result["dataset_id"] == str(ds.id)
        assert spy_revoke == ["sdg-task"]
        db.commit.assert_awaited_once()

    @pytest.mark.parametrize("status", _TERMINAL)
    async def test_idempotent_on_terminal(self, spy_revoke, status) -> None:
        ds = _dataset(status)
        db = _db(ds)

        result = await datasets_service.cancel_dataset(db, ds.id)

        assert result["status"] == status.value
        assert ds.status is status, "a finished job must not be mutated"
        assert spy_revoke == [], "no revoke for an already-finished job"
        db.commit.assert_not_awaited()

    async def test_falls_back_to_jsonb_task_id(self, spy_revoke) -> None:
        """Rows predating the 0006 backfill only carry the id in JSONB."""
        ds = _dataset(
            JobStatus.RUNNING,
            task_id=None,
            meta={"celery_task_id": "legacy-task"},
        )

        await datasets_service.cancel_dataset(_db(ds), ds.id)

        assert spy_revoke == ["legacy-task"]

    async def test_seed_upload_reports_already_complete(self, spy_revoke) -> None:
        """Seed uploads are persisted COMPLETED synchronously — nothing to cancel."""
        ds = Dataset(
            id=uuid4(),
            project_id=uuid4(),
            name="seed",
            source=DatasetSource.SEED,
            task_type=TaskType.QA,
            status=JobStatus.COMPLETED,
        )

        result = await datasets_service.cancel_dataset(_db(ds), ds.id)

        assert result["status"] == "completed"
        assert spy_revoke == []


class TestCancelExport:
    async def test_404_when_artifact_missing(self, spy_revoke) -> None:
        with pytest.raises(HTTPException) as exc:
            await model_service.cancel_export(_db(None), uuid4())
        assert exc.value.status_code == 404

    async def test_409_when_no_export_was_ever_requested(self, spy_revoke) -> None:
        """`export_status is None` means no job exists — reporting a CANCELLED
        transition for it would be a lie."""
        art = _artifact(None, task_id=None)

        with pytest.raises(HTTPException) as exc:
            await model_service.cancel_export(_db(art), art.id)

        assert exc.value.status_code == 409
        assert spy_revoke == []

    @pytest.mark.parametrize("status", _ACTIVE)
    async def test_flips_export_status(self, spy_revoke, status) -> None:
        art = _artifact(status)
        db = _db(art)

        result = await model_service.cancel_export(db, art.id)

        assert art.export_status is JobStatus.CANCELLED
        assert result["artifact_id"] == str(art.id)
        assert spy_revoke == ["exp-task"]
        db.commit.assert_awaited_once()

    @pytest.mark.parametrize("status", _TERMINAL)
    async def test_idempotent_on_terminal(self, spy_revoke, status) -> None:
        art = _artifact(status)
        db = _db(art)

        result = await model_service.cancel_export(db, art.id)

        assert result["status"] == status.value
        assert art.export_status is status
        assert spy_revoke == []
        db.commit.assert_not_awaited()

    async def test_does_not_touch_the_legacy_completion_fields(self, spy_revoke) -> None:
        """`gguf_uri` / `export_error_message` stay the contract FE reads."""
        art = _artifact(JobStatus.RUNNING)
        art.gguf_uri = "s3://models/x.gguf"

        await model_service.cancel_export(_db(art), art.id)

        assert art.gguf_uri == "s3://models/x.gguf"
        assert art.export_error_message is None


class TestCancelEvaluation:
    async def test_404_when_missing(self, spy_revoke) -> None:
        with pytest.raises(HTTPException) as exc:
            await evaluation_service.cancel_evaluation(_db(None), uuid4())
        assert exc.value.status_code == 404

    @pytest.mark.parametrize("status", _ACTIVE)
    async def test_flips_and_stamps_ended_at(self, spy_revoke, status) -> None:
        ev = _evaluation(status)
        db = _db(ev)

        result = await evaluation_service.cancel_evaluation(db, ev.id)

        assert ev.status is JobStatus.CANCELLED
        assert ev.ended_at is not None
        assert result["evaluation_id"] == str(ev.id)
        assert spy_revoke == ["eval-task"]

    @pytest.mark.parametrize("status", _TERMINAL)
    async def test_idempotent_on_terminal(self, spy_revoke, status) -> None:
        ev = _evaluation(status)
        result = await evaluation_service.cancel_evaluation(_db(ev), ev.id)

        assert result["status"] == status.value
        assert spy_revoke == []


class TestCancelTrainingUnchanged:
    async def test_still_flips_and_revokes(self, spy_revoke) -> None:
        job = _training(JobStatus.RUNNING)
        db = _db(job)

        result = await trainings_service.cancel_training(db, job.id)

        assert job.status is JobStatus.CANCELLED
        assert job.ended_at is not None
        assert result == {"training_id": str(job.id), "status": "cancelled"}
        assert spy_revoke == ["train-task"]

    @pytest.mark.parametrize("status", _TERMINAL)
    async def test_idempotency_preserved_after_refactor(self, spy_revoke, status) -> None:
        job = _training(status)
        result = await trainings_service.cancel_training(_db(job), job.id)

        assert result["status"] == status.value
        assert spy_revoke == []

    async def test_404_preserved(self, spy_revoke) -> None:
        with pytest.raises(HTTPException) as exc:
            await trainings_service.cancel_training(_db(None), uuid4())
        assert exc.value.status_code == 404


# =============================================================================
# Routing contract
# =============================================================================


@pytest.fixture(scope="module")
def paths() -> dict:
    from api.main import app

    return app.openapi()["paths"]


class TestRoutes:
    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/datasets/{dataset_id}/cancel",
            "/api/v1/models/{model_id}/export/cancel",
            "/api/v1/evaluations/{evaluation_id}/cancel",
            "/api/v1/trainings/{training_id}/cancel",
        ],
    )
    def test_cancel_route_exists_as_post(self, paths: dict, path: str) -> None:
        assert path in paths, f"missing cancel route: {path}"
        assert "post" in paths[path]

    def test_delete_trainings_still_exists(self, paths: dict) -> None:
        """smart-model-tune calls this today (engineApi.ts:286) — do not break it."""
        assert "delete" in paths["/api/v1/trainings/{training_id}"]

    def test_dataset_delete_still_means_delete_not_cancel(self, paths: dict) -> None:
        """DELETE /datasets/{id} was already taken by row deletion, which is why
        cancel had to be a separate POST path."""
        assert "delete" in paths["/api/v1/datasets/{dataset_id}"]

    def test_export_cancel_is_not_shadowed_by_export(self, paths: dict) -> None:
        assert "/api/v1/models/{model_id}/export" in paths
        assert "/api/v1/models/{model_id}/export/cancel" in paths
