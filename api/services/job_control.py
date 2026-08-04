"""Shared job-control helpers for the `/cancel` endpoints across resources.

`cancel_training` (`api/services/trainings_service.py`) was the first cancel
endpoint and had this logic inline. This module extracts the reusable bits so
`datasets_service.cancel_dataset`, `model_service.cancel_export`,
`evaluation_service.cancel_evaluation`, and `trainings_service.cancel_training`
all agree on:
  - what counts as "already finished" (no revoke needed, just report status)
  - how a Celery revoke call is made and how its failures are handled
"""

from __future__ import annotations

import logging

from api.schemas.enums import JobStatus

log = logging.getLogger(__name__)

# Statuses past which a cancel request is a no-op: idempotent 200 with the
# current status, no revoke call, no further mutation. All four cancel
# service functions test their resource's status/export_status against this
# set so "already terminal" means the same thing everywhere.
TERMINAL_JOB_STATUSES: frozenset[JobStatus] = frozenset(
    {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
)


def revoke_celery_task(task_id: str | None, *, context: str) -> None:
    """Best-effort ``celery.control.revoke(terminate=True, SIGTERM)``.

    No-op when ``task_id`` is falsy (e.g. a row that never got a Celery task
    id). Any broker error (Redis down, network hiccup) is logged and
    swallowed — callers must still flip the resource's status to CANCELLED
    even when the revoke call itself failed, otherwise a broker hiccup would
    leave a job permanently un-cancellable from the API's point of view.

    Deliberately does NOT publish a WebSocket terminal frame here.
    ``revoke(terminate=True, signal="SIGTERM")`` kills the worker task via
    signal while it's mid-run; that raises inside the task body, and the
    task's own ``except`` block already publishes a ``JobFailed`` frame for
    that job id (SDG generation, model export, evaluation all follow this
    pattern). Publishing a second terminal frame from here would race that
    one and deliver two frames to anyone connected on ``/ws/jobs/{id}`` — do
    not "fix" this by adding a publish call in this function.
    """
    if not task_id:
        return
    try:
        # Local import — Celery is in base deps but this keeps test-only
        # imports tidy (mirrors the historical inline import that used to
        # live directly in trainings_service.cancel_training).
        from workers.celery_app import celery_app

        celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
    except Exception:  # proceed to flip status even on broker hiccup
        log.warning(
            "could not revoke celery task %s for %s",
            task_id,
            context,
            exc_info=True,
        )


__all__ = ["TERMINAL_JOB_STATUSES", "revoke_celery_task"]
