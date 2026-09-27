"""Celery task: preload a `Deployment` onto the shared Ollama daemon.

A `Deployment` (see `api/models/deployment.py`) is a logical serving slot
layered on top of Ollama's own model registry: `status=RUNNING` means the
model is pinned in memory (`keep_alive=-1`); `status=COMPLETED` means it has
been cleanly unloaded (`keep_alive=0`) — a stopped deployment, not a failed
one. This module drives only the *preload* (pin) step; unloading is a single
fast HTTP call driven synchronously from `api/services/deployments_service.py`
(T3) via the same `OllamaClient.set_keep_alive` — no separate "unload" task
is needed since it doesn't download or register anything.

Flow:
  1. Load the `Deployment` row. Anything other than PENDING (already
     stopped/cancelled/re-run) is a no-op — the row moved on without this
     task's help, so there is nothing to preload and nothing to report.
  2. Resolve the Ollama tag via the linked `ModelArtifact`. No tag is a
     failure (nothing servable to pin).
  3. `set_keep_alive(tag, -1)` — outside any open DB session, since this is
     a network call to Ollama, not a DB operation.
  4. Conditional UPDATE `PENDING -> RUNNING`. Zero rows affected means the
     stop endpoint won the race and moved the deployment off PENDING while
     step 3 was in flight — best-effort unload what we just pinned (a VRAM
     leak, not data loss, if that fails) and report failure instead of
     leaving a phantom pinned model for a deployment nothing will ever mark
     RUNNING.
  5. Audit + publish `JobCompleted`.

Any exception above (including the "no tag" case) lands in the handler at
the bottom: conditional `PENDING -> FAILED` + audit + a `JobFailed` frame,
then re-raise so Celery records the task failure (`max_retries=0`, so no
retry storm). The handler catches `BaseException`, not `Exception` — see
`api/services/job_control.py`'s module docstring for why this matters:
`stop_deployment`'s revoke (SIGTERM) surfaces as a `SystemExit` raised
inside this task body, and a narrower handler would both skip the terminal
frame and skip the pin-cleanup below. It also tracks whether the `-1` pin
landed; if a `SystemExit` (or anything else) arrives after the pin but
before the conditional `PENDING -> RUNNING` UPDATE commits, the handler
best-effort unloads (`keep_alive=0`) what it just pinned, same as the
in-band "lost the race" branch in step 4 — otherwise that window leaks a
pinned model no code will ever unload. Only `completed`/`failed` frames are
ever published — there is no meaningful progress phase for a single HTTP
call.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from celery.utils.log import get_task_logger
from sqlalchemy import update

from api.core import request_context
from api.core.config import get_settings
from api.models.deployment import Deployment
from api.models.model_artifact import ModelArtifact
from api.schemas.enums import JobStatus
from api.schemas.progress import JobCompleted, JobFailed
from api.services import audit_service
from workers.celery_app import celery_app
from workers.ollama_client import OllamaClient
from workers.progress import publish_ws_message, sync_redis_scope
from workers.sync_db import session_scope

log = get_task_logger(__name__)


@celery_app.task(bind=True, name="deployment.preload", max_retries=0)
def preload_deployment(self, deployment_id: str) -> dict[str, Any]:
    """Pin a deployment's model in Ollama and flip PENDING -> RUNNING."""
    job_id: str = self.request.id
    deployment_uuid = UUID(deployment_id)
    settings = get_settings()
    tag: str | None = None
    pinned = False  # did `set_keep_alive(tag, -1)` succeed? guards the handler's cleanup below.

    with sync_redis_scope() as redis:

        def publish(msg: Any) -> None:
            publish_ws_message(redis, job_id, msg)

        try:
            with session_scope() as session:
                deployment = session.get(Deployment, deployment_uuid)
                if deployment is None or deployment.status != JobStatus.PENDING:
                    # Already stopped/cancelled/handled — nothing to do, and
                    # no terminal frame for a job that never really started.
                    return {"skipped": True}
                owner_id = deployment.owner_id
                model_artifact_id = deployment.model_artifact_id
                artifact = (
                    session.get(ModelArtifact, model_artifact_id)
                    if model_artifact_id is not None
                    else None
                )
                tag = artifact.ollama_model_tag if artifact is not None else None

            if not tag:
                raise RuntimeError(
                    f"deployment {deployment_id} has no servable Ollama model tag"
                )

            ollama = OllamaClient(str(settings.ollama_base_url))
            ollama.set_keep_alive(tag, -1)
            pinned = True

            with session_scope() as session:
                result = session.execute(
                    update(Deployment)
                    .where(
                        Deployment.id == deployment_uuid,
                        Deployment.status == JobStatus.PENDING,
                    )
                    .values(status=JobStatus.RUNNING)
                )
                if result.rowcount == 0:
                    try:
                        ollama.set_keep_alive(tag, 0)
                    except Exception:
                        log.warning(
                            "preload: failed to unload %s after losing the "
                            "PENDING race (job=%s, deployment=%s)",
                            tag,
                            job_id,
                            deployment_id,
                            exc_info=True,
                        )
                    publish(
                        JobFailed(
                            job_id=job_id,
                            error="deployment was stopped before it became active",
                            error_type="Cancelled",
                        )
                    )
                    return {"status": "cancelled", "deployment_id": deployment_id}

                audit_service.record(
                    session,
                    action="deployment.preload",
                    resource_type="deployment",
                    resource_id=deployment_id,
                    actor_id=request_context.current_user_id() or owner_id,
                    request_id=request_context.current_request_id(),
                    metadata={"model_tag": tag},
                )

            publish(
                JobCompleted(
                    job_id=job_id,
                    result={
                        "deployment_id": deployment_id,
                        "status": "running",
                        "model_tag": tag,
                    },
                    model_artifact_id=model_artifact_id,
                )
            )
            return {"status": "running", "deployment_id": deployment_id, "model_tag": tag}

        except BaseException as exc:
            log.exception(
                "preload task failed (job=%s, deployment=%s)", job_id, deployment_id
            )
            row_was_pending = False
            try:
                with session_scope() as fail_session:
                    result = fail_session.execute(
                        update(Deployment)
                        .where(
                            Deployment.id == deployment_uuid,
                            Deployment.status == JobStatus.PENDING,
                        )
                        .values(
                            status=JobStatus.FAILED,
                            error_message=(str(exc) or repr(exc))[:4000],
                        )
                    )
                    row_was_pending = bool(result.rowcount)
                    if result.rowcount:
                        audit_service.record(
                            fail_session,
                            action="deployment.preload_failed",
                            resource_type="deployment",
                            resource_id=deployment_id,
                            outcome="failure",
                            actor_id=request_context.current_user_id(),
                            request_id=request_context.current_request_id(),
                            metadata={"job_id": job_id, "error_type": type(exc).__name__},
                        )
            except Exception:
                log.warning(
                    "could not persist FAILED status for deployment %s",
                    deployment_id,
                    exc_info=True,
                )
            # `row_was_pending` true means the row was still PENDING when we
            # just flipped it to FAILED, i.e. the conditional PENDING ->
            # RUNNING UPDATE above never landed (or lost its own race and got
            # rolled back with it) — so whatever we pinned is now orphaned:
            # nothing will ever unload it. If the row wasn't PENDING here, it's
            # either already legitimately RUNNING (leave the pin alone) or
            # someone else (a stop) already unloaded it.
            if pinned and row_was_pending and tag:
                try:
                    ollama.set_keep_alive(tag, 0)
                except Exception:
                    log.warning(
                        "preload: failed to unload %s after a %s during preload "
                        "(job=%s, deployment=%s)",
                        tag,
                        type(exc).__name__,
                        job_id,
                        deployment_id,
                        exc_info=True,
                    )
            try:
                publish(
                    JobFailed(
                        job_id=job_id,
                        error=str(exc) or repr(exc),
                        error_type=type(exc).__name__,
                    )
                )
            except Exception:
                log.warning("failed to publish JobFailed", exc_info=True)
            raise


__all__ = ["preload_deployment"]
