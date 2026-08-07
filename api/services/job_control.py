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
    signal while it's mid-run; billiard's child-side handler turns that into a
    ``SystemExit`` raised inside the task body, and the task's own handler
    publishes a ``JobFailed`` frame for that job id. Publishing a second
    terminal frame from here would race that one and deliver two frames to
    anyone connected on ``/ws/jobs/{id}`` — do not "fix" this by adding a
    publish call in this function.

    **That guarantee only holds because all five cancellable task bodies
    catch ``BaseException``, not ``Exception``.** ``SystemExit`` derives from
    ``BaseException``, so a task that narrows its handler silently stops
    emitting a terminal frame on cancel *and* leaves its row's status
    wherever it was. This was measured, not assumed: on a live worker a
    cancelled export reports ``error_message == "-241"``
    (``sys.exit(-(256 - 15))``). Before the widening, cancelling SDG or an
    evaluation produced no terminal frame at all.

    The widening landed in two passes, which is itself the lesson: ``523aded``
    covered ``data_generation`` / ``evaluation`` / ``model_export``, and
    ``training`` / ``hpo_training`` were missed until a GPU-box run caught
    them a release later — a cancelled training left its row ``cancelled``
    while ``job:{id}:last`` still held a mid-run ``training_progress`` frame,
    so a WebSocket-only client waited forever. If you touch the ``except``
    clause in any of
    ``workers/tasks/{data_generation,evaluation,model_export,training,hpo_training}.py``,
    keep it at ``BaseException`` and keep the ``!= JobStatus.CANCELLED`` guard
    — without the guard, widening turns every cancel into ``failed``. The
    parametrized guard in ``tests/unit/test_worker_progress_frames.py``
    (``_CANCELLABLE_TASKS``) enforces both across all five files at once.
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
