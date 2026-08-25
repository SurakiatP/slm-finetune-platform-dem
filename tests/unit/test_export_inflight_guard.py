"""`POST /models/{id}/export` must refuse a second export while one is running.

Without the guard the second call overwrote `export_celery_task_id`, which
orphaned the first Celery task: nothing held its id any more, so
`/export/cancel` could never revoke it, and the progress frames it kept
publishing to `job:{old_id}` went to a channel no client was subscribed to.
The job ran to completion on the GPU with nobody watching and nobody able to
stop it.

The guard must NOT block re-exporting a finished artifact — that is a normal
thing to do (different quantization, or a retry after a failure), and
over-blocking would be its own bug.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from api.core.auth import CurrentUser
from api.models.model_artifact import ModelArtifact
from api.schemas.artifacts import ModelExportRequest
from api.schemas.enums import ArtifactFormat, JobStatus
from api.services import model_service

USER = CurrentUser(id="user-a-sub", email="a@example.com")
IN_FLIGHT_JOB = "job-already-running"

_BLOCKED = [JobStatus.PENDING, JobStatus.RUNNING]
_ALLOWED = [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED, None]


def _artifact(export_status, error_message: str | None = None) -> ModelArtifact:
    return ModelArtifact(
        id=uuid4(),
        training_job_id=uuid4(),
        name="m",
        base_model="unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
        lora_adapter_uri="s3://models/adapters/x",
        export_status=export_status,
        export_celery_task_id=IN_FLIGHT_JOB if export_status else None,
        export_error_message=error_message,
    )


def _db(row) -> MagicMock:
    db = MagicMock()

    async def _get(model, ident, *a, **kw):  # noqa: ANN001, ANN002, ANN003
        return row if isinstance(row, model) else None

    db.get = AsyncMock(side_effect=_get)
    db.commit = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.fixture
def spy_apply(monkeypatch):
    """Records every enqueue so 'no task was started' is provable, not assumed."""
    calls: list[dict] = []

    class _Result:
        id = "job-newly-created"

    def _apply_async(**kwargs):
        calls.append(kwargs)
        return _Result()

    import workers.tasks.model_export as export_task

    monkeypatch.setattr(export_task.export_model, "apply_async", _apply_async)
    return calls


@pytest.fixture(autouse=True)
def _stub_ownership(monkeypatch):
    async def _assert(db, model_id, user):  # noqa: ANN001
        return await db.get(ModelArtifact, model_id)

    monkeypatch.setattr(model_service.ownership, "assert_model_access", _assert)


@pytest.fixture(autouse=True)
def _stub_quota(monkeypatch):
    """This file is about the in-flight 409 guard in isolation — the GPU
    quota gate (429) that now sits right after it is exercised separately
    in `tests/unit/test_gpu_quota_guards.py`, including the ordering case
    where both guards would otherwise fire. Stubbing it here as a no-op
    keeps this file's `_db()` MagicMock (no real SQLAlchemy execution)
    valid: `quota.assert_can_submit` issues real `db.execute(...)` calls
    that a bare MagicMock can't satisfy.
    """
    stub = AsyncMock(return_value=None)
    monkeypatch.setattr(model_service.quota, "assert_can_submit", stub)
    return stub


class TestSecondExportIsRefused:
    @pytest.mark.parametrize("status", _BLOCKED)
    async def test_409_while_in_flight(self, spy_apply, status) -> None:
        artifact = _artifact(status)
        db = _db(artifact)

        with pytest.raises(HTTPException) as exc:
            await model_service.submit_export_job(
                db,
                model_id=artifact.id,
                request=ModelExportRequest(format=ArtifactFormat.GGUF),
                user=USER,
            )

        assert exc.value.status_code == 409
        assert IN_FLIGHT_JOB in exc.value.detail, "the caller needs the job id to cancel it"

    @pytest.mark.parametrize("status", _BLOCKED)
    async def test_no_task_is_enqueued(self, spy_apply, status) -> None:
        artifact = _artifact(status)
        with pytest.raises(HTTPException):
            await model_service.submit_export_job(
                _db(artifact),
                model_id=artifact.id,
                request=ModelExportRequest(format=ArtifactFormat.GGUF),
                user=USER,
            )
        assert spy_apply == [], "a refused export must not spend GPU time"

    @pytest.mark.parametrize("status", _BLOCKED)
    async def test_the_in_flight_job_id_survives(self, spy_apply, status) -> None:
        """THE regression. Overwriting this is what orphaned the first job."""
        artifact = _artifact(status)
        with pytest.raises(HTTPException):
            await model_service.submit_export_job(
                _db(artifact),
                model_id=artifact.id,
                request=ModelExportRequest(format=ArtifactFormat.GGUF),
                user=USER,
            )
        assert artifact.export_celery_task_id == IN_FLIGHT_JOB
        assert artifact.export_status is status


class TestReExportIsStillAllowed:
    @pytest.mark.parametrize("status", _ALLOWED)
    async def test_terminal_or_never_exported_proceeds(self, spy_apply, status) -> None:
        """Over-blocking would be its own bug — a finished export must not
        permanently prevent producing another artifact."""
        artifact = _artifact(status)
        resp = await model_service.submit_export_job(
            _db(artifact),
            model_id=artifact.id,
            request=ModelExportRequest(format=ArtifactFormat.GGUF),
            user=USER,
        )
        assert len(spy_apply) == 1
        assert resp.job_id == "job-newly-created"
        assert artifact.export_status is JobStatus.PENDING


class TestStaleExportErrorIsCleared:
    """T-B1 (blocker #10): a re-export must not keep serving a prior export's
    failure message once a new job is PENDING/RUNNING. `export_error_message`
    is read by the frontend regardless of `export_status`, and the worker
    only clears it on SUCCESS — so `submit_export_job` must clear it itself
    on every successful resubmit.
    """

    @pytest.mark.parametrize("status", [JobStatus.FAILED, JobStatus.CANCELLED])
    async def test_stale_error_cleared_on_resubmit(self, spy_apply, status) -> None:
        artifact = _artifact(status, error_message="old boom")

        resp = await model_service.submit_export_job(
            _db(artifact),
            model_id=artifact.id,
            request=ModelExportRequest(format=ArtifactFormat.GGUF),
            user=USER,
        )

        assert artifact.export_error_message is None
        assert artifact.export_status is JobStatus.PENDING
        assert artifact.export_celery_task_id == "job-newly-created"
        assert len(spy_apply) == 1
        assert resp.job_id == "job-newly-created"

    async def test_refused_submit_does_not_touch_error_message(self, spy_apply) -> None:
        """A 409-refused submit (export already in flight) must not mutate
        the row at all — including the stale error message."""
        artifact = _artifact(JobStatus.RUNNING, error_message="old boom")

        with pytest.raises(HTTPException) as exc:
            await model_service.submit_export_job(
                _db(artifact),
                model_id=artifact.id,
                request=ModelExportRequest(format=ArtifactFormat.GGUF),
                user=USER,
            )

        assert exc.value.status_code == 409
        assert artifact.export_error_message == "old boom"
        assert spy_apply == []
