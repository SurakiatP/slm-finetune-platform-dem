"""Application service for the `/api/v1/deployments` REST resource.

A Deployment is a logical serving slot on the shared Ollama daemon (one GPU,
no per-deployment isolation): `create_deployment` reserves the row and
enqueues a preload; `workers/tasks/deployment.py::preload_deployment` (T4)
pins the model in VRAM (`keep_alive=-1`) and flips PENDING -> RUNNING.
Stopping (`stop_deployment`, or the model-delete hook `stop_for_artifact`)
unloads it (`keep_alive=0`) and flips RUNNING -> COMPLETED — "completed" here
means cleanly stopped, not failed, exactly like `api/models/deployment.py`
documents.

Status reuses the shared `job_status` enum: pending -> running (= active) ->
completed (= stopped) / failed / cancelled.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.core import request_context
from api.core.auth import CurrentUser
from api.core.config import get_settings
from api.models.deployment import Deployment
from api.models.model_artifact import ModelArtifact
from api.schemas.deployments import DeploymentCreate, DeploymentResponse, DeploymentUpdate
from api.schemas.enums import JobStatus
from api.schemas.responses import Page
from api.services import audit_service, ownership
from api.services.job_control import TERMINAL_JOB_STATUSES, revoke_celery_task
from workers.ollama_client import OllamaClient

log = logging.getLogger(__name__)

# A deployment counts against the active caps (create-time quota, "does this
# artifact already have one") for exactly these two statuses — mirrors
# `api/services/quota.py`'s `_IN_FLIGHT` for the same reason: terminal states
# are done consuming the shared GPU/Ollama slot.
ACTIVE = (JobStatus.PENDING, JobStatus.RUNNING)

# Timeout for the unload call driven from the request path (stop / delete /
# model-delete hook) — short on purpose: this is a best-effort cleanup call,
# not something a caller should be stuck waiting 600s (OllamaClient's default)
# for. The preload path (`workers/tasks/deployment.py`) uses the daemon
# default timeout instead, since a cold model load can legitimately take a
# while and that call runs on a worker, not in front of an HTTP client.
_UNLOAD_TIMEOUT_SECONDS = 30.0


# ---- create -----------------------------------------------------------------


async def create_deployment(
    db: AsyncSession, body: DeploymentCreate, user: CurrentUser
) -> DeploymentResponse:
    """Reserve a Deployment row and enqueue its preload.

    Ownership + exportedness checked first (spending a deployment slot on an
    artifact the caller doesn't own, or one with nothing servable, is the
    attack/waste that matters most). Then: at most one ACTIVE deployment per
    artifact, then the global cap, then the per-owner cap (cheapest/most
    likely-to-reject checks first, same ordering rationale as
    `sdg_service.submit_sdg_job`'s quota-last ordering, just applied to a
    resource with more create-time gates than a plain quota check).
    """
    artifact = await ownership.assert_model_access(db, body.model_artifact_id, user)
    if not artifact.ollama_model_tag:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Model {artifact.id} has not been exported to Ollama yet. "
                f"POST /api/v1/models/{artifact.id}/export first."
            ),
        )

    existing = (
        await db.execute(
            select(Deployment.id)
            .where(Deployment.model_artifact_id == artifact.id, Deployment.status.in_(ACTIVE))
            .limit(1)
        )
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Model {artifact.id} already has an active deployment "
                f"({existing[0]}); stop it first."
            ),
        )

    settings = get_settings()

    # ponytail: count-then-insert race — two concurrent creates can both pass
    # both cap checks below before either commits its row, so the caps are
    # "best effort, usually exact" rather than airtight. Acceptable given how
    # small these caps are by design (default 1/user, 3 global) and how low
    # deployment-create volume is; upgrade to a `SELECT ... FOR UPDATE`-style
    # serializing lock (or a Postgres advisory lock, like
    # `templates_service.CLEANUP_LOCK`) if that ever stops being true.
    global_count = int(
        (
            await db.execute(
                select(func.count()).select_from(Deployment).where(Deployment.status.in_(ACTIVE))
            )
        ).scalar_one()
    )
    if global_count >= settings.deployment_max_active_global:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Global deployment quota reached "
                f"({global_count}/{settings.deployment_max_active_global} active). "
                "Try again shortly."
            ),
            headers={"Retry-After": str(settings.quota_retry_after_seconds)},
        )

    owner_count = int(
        (
            await db.execute(
                select(func.count())
                .select_from(Deployment)
                .where(Deployment.status.in_(ACTIVE), Deployment.owner_id == user.id)
            )
        ).scalar_one()
    )
    if owner_count >= settings.deployment_max_active_per_user:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Your deployment quota reached "
                f"({owner_count}/{settings.deployment_max_active_per_user} active). "
                "Try again shortly."
            ),
            headers={"Retry-After": str(settings.quota_retry_after_seconds)},
        )

    # Generated up front (not read back off `apply_async`'s `AsyncResult`
    # afterwards, unlike `model_service.submit_export_job`) so the row can be
    # inserted and committed *before* the enqueue call — see the try/except
    # below for why that ordering matters here.
    job_id = str(uuid4())
    deployment = Deployment(
        owner_id=user.id,
        model_artifact_id=artifact.id,
        name=body.name or artifact.name,
        rate_limit_per_min=settings.deployment_default_rate_limit_per_min,
        celery_task_id=job_id,
    )
    db.add(deployment)
    await db.flush()  # populate deployment.id for the audit row below
    audit_service.record(
        db,
        action="deployment.create",
        resource_type="deployment",
        resource_id=str(deployment.id),
        actor_id=request_context.current_user_id(),
        request_id=request_context.current_request_id(),
        metadata={"model_artifact_id": str(artifact.id), "job_id": job_id},
    )
    await db.commit()

    # Enqueue only after the row is committed: if the broker is unreachable
    # the row must still exist (as FAILED, with a job id a caller can look
    # up), not vanish along with a rolled-back transaction.
    try:
        await _enqueue_preload(deployment.id, job_id)
    except Exception as exc:
        log.warning(
            "failed to enqueue deployment preload for %s", deployment.id, exc_info=True
        )
        deployment.status = JobStatus.FAILED
        deployment.error_message = (str(exc) or repr(exc))[:4000]
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="failed to enqueue deployment preload job; try again shortly",
        ) from exc

    return _to_response(deployment, artifact.ollama_model_tag)


async def _enqueue_preload(deployment_id: UUID, job_id: str) -> None:
    """`apply_async` the T4 preload task, pinning its Celery task id to `job_id`.

    Local import: `workers.tasks.deployment` is worker-only code, and this
    keeps the API process from eagerly loading it at module import time —
    same reasoning as `model_service.submit_export_job`'s local import of
    `workers.tasks.model_export`.
    """
    from workers.tasks.deployment import preload_deployment

    preload_deployment.apply_async(kwargs={"deployment_id": str(deployment_id)}, task_id=job_id)


# ---- list / get ---------------------------------------------------------------


async def list_deployments(
    db: AsyncSession,
    user: CurrentUser,
    *,
    status_filter: JobStatus | None,
    limit: int,
    offset: int,
) -> Page[DeploymentResponse]:
    """List the caller's own deployments, newest first."""
    base = (
        select(Deployment, ModelArtifact.ollama_model_tag)
        .outerjoin(ModelArtifact, ModelArtifact.id == Deployment.model_artifact_id)
        .where(Deployment.owner_id == user.id)
        .order_by(Deployment.created_at.desc())
    )
    count = select(func.count()).select_from(Deployment).where(Deployment.owner_id == user.id)
    if status_filter is not None:
        base = base.where(Deployment.status == status_filter)
        count = count.where(Deployment.status == status_filter)

    total = int((await db.execute(count)).scalar_one())
    rows = (await db.execute(base.limit(limit).offset(offset))).all()
    items = [_to_response(deployment, tag) for deployment, tag in rows]
    return Page[DeploymentResponse](items=items, total=total, limit=limit, offset=offset)


async def get_deployment(
    db: AsyncSession, deployment_id: UUID, user: CurrentUser
) -> DeploymentResponse:
    deployment = await _get_owned(db, deployment_id, user)
    tag = await _model_tag_for(db, deployment)
    return _to_response(deployment, tag)


# ---- update -------------------------------------------------------------------


async def update_deployment(
    db: AsyncSession, deployment_id: UUID, body: DeploymentUpdate, user: CurrentUser
) -> DeploymentResponse:
    deployment = await _get_owned(db, deployment_id, user)
    settings = get_settings()

    if body.rate_limit_per_min is not None:
        if body.rate_limit_per_min > settings.deployment_max_rate_limit_per_min:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"rate_limit_per_min may not exceed "
                    f"{settings.deployment_max_rate_limit_per_min}"
                ),
            )
        deployment.rate_limit_per_min = body.rate_limit_per_min
    if body.name is not None:
        deployment.name = body.name

    audit_service.record(
        db,
        action="deployment.update",
        resource_type="deployment",
        resource_id=str(deployment.id),
        actor_id=request_context.current_user_id(),
        request_id=request_context.current_request_id(),
        metadata=body.model_dump(exclude_none=True),
    )
    await db.commit()
    # `updated_at` is a server-side `onupdate=func.now()` column (see
    # `api/models/base.py::TimestampMixin`) — unlike the INSERT path, the
    # UPDATE flush here doesn't come back with its new value already
    # populated, so it's left expired. Refresh before building the response,
    # same pattern as `datasets_service`/`projects_service`.
    await db.refresh(deployment)

    tag = await _model_tag_for(db, deployment)
    return _to_response(deployment, tag)


# ---- stop / delete --------------------------------------------------------


async def stop_deployment(
    db: AsyncSession, deployment_id: UUID, user: CurrentUser
) -> DeploymentResponse:
    """Idempotent: a deployment already in a terminal state is a no-op, not
    an error — same "already finished" contract as
    `job_control`/`model_service.cancel_export` for the other job-shaped
    resources.
    """
    deployment = await _get_owned(db, deployment_id, user)
    tag = await _model_tag_for(db, deployment)

    if deployment.status in TERMINAL_JOB_STATUSES:
        return _to_response(deployment, tag)

    await _apply_stop(db, deployment, tag)
    audit_service.record(
        db,
        action="deployment.stop",
        resource_type="deployment",
        resource_id=str(deployment.id),
        actor_id=request_context.current_user_id(),
        request_id=request_context.current_request_id(),
        metadata={"model_tag": tag},
    )
    await db.commit()
    # See the matching comment in `update_deployment`: `_apply_stop` changed
    # `status`, which expires the server-side `updated_at` default post-flush.
    await db.refresh(deployment)
    return _to_response(deployment, tag)


async def delete_deployment(db: AsyncSession, deployment_id: UUID, user: CurrentUser) -> None:
    """Hard-delete a deployment, stopping it first if it's still ACTIVE.

    One audit row (`deployment.delete`), not two — the stop performed here
    is folded into the delete rather than routed through `stop_deployment`
    (which would additionally record `deployment.stop` and commit on its
    own), since the intent recorded is "this deployment was deleted", not
    "stopped, then separately deleted".
    """
    deployment = await _get_owned(db, deployment_id, user)
    if deployment.status in ACTIVE:
        tag = await _model_tag_for(db, deployment)
        await _apply_stop(db, deployment, tag)

    audit_service.record(
        db,
        action="deployment.delete",
        resource_type="deployment",
        resource_id=str(deployment.id),
        actor_id=request_context.current_user_id(),
        request_id=request_context.current_request_id(),
        metadata={"name": deployment.name},
    )
    await db.delete(deployment)
    await db.commit()


async def stop_for_artifact(db: AsyncSession, artifact: ModelArtifact) -> None:
    """Stop every ACTIVE deployment of `artifact`. Called by
    `model_service.purge_artifact` before it deletes the Ollama tag.

    MUST NOT commit — same contract as `purge_artifact` itself: the caller
    (`model_service.delete_model`) owns the transaction boundary and commits
    once, after the ORM delete.
    """
    deployments = (
        await db.execute(
            select(Deployment).where(
                Deployment.model_artifact_id == artifact.id, Deployment.status.in_(ACTIVE)
            )
        )
    ).scalars().all()
    for deployment in deployments:
        await _apply_stop(db, deployment, artifact.ollama_model_tag)
        audit_service.record(
            db,
            action="deployment.stop",
            resource_type="deployment",
            resource_id=str(deployment.id),
            actor_id=request_context.current_user_id(),
            request_id=request_context.current_request_id(),
            metadata={"reason": "model_deleted"},
        )


# ---- shared helpers ---------------------------------------------------------


async def _apply_stop(db: AsyncSession, deployment: Deployment, tag: str | None) -> None:
    """Transition a non-terminal deployment to its stopped state.

    Caller has already established `deployment.status` is PENDING or RUNNING
    (the only two members of `ACTIVE`) — but for PENDING that in-memory read
    can be stale: `workers/tasks/deployment.py::preload_deployment` (T4) runs
    its own conditional `PENDING -> RUNNING` UPDATE concurrently, and if that
    lands between our caller's read and here, blindly overwriting the row to
    CANCELLED would leave the worker's just-pinned (`keep_alive=-1`) model
    with nothing left to unload it — a VRAM leak. So the PENDING branch races
    the worker with its own conditional UPDATE instead of trusting the
    in-memory status: win it (rowcount 1) and this is a normal cancel — only
    *then* revoke the Celery task, so a losing revoke can never land on a
    task that's about to legitimately become RUNNING. Lose it (rowcount 0,
    the worker's UPDATE got there first) and re-read the row instead of
    guessing: if it's now RUNNING, fall through to the unload+COMPLETED
    branch below; if it's already terminal (stopped some other way), leave
    it alone. `synchronize_session=False` because we set the ORM attribute
    ourselves on each branch — never off the stale PENDING value already in
    memory — so a later flush can't write that stale status back.

    Does not commit — same contract as before, callers commit.
    """
    if deployment.status == JobStatus.PENDING:
        result = await db.execute(
            update(Deployment)
            .where(Deployment.id == deployment.id, Deployment.status == JobStatus.PENDING)
            .values(status=JobStatus.CANCELLED)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 1:
            revoke_celery_task(deployment.celery_task_id, context=f"deployment {deployment.id}")
            deployment.status = JobStatus.CANCELLED
            return
        await db.refresh(deployment)
        if deployment.status != JobStatus.RUNNING:
            return  # already terminal via some other path — leave it alone

    if tag:
        await _unload(tag)
    deployment.status = JobStatus.COMPLETED


async def _unload(tag: str) -> None:
    """Best-effort `keep_alive=0` unload — never blocks a stop/delete on Ollama.

    Reuses `OllamaClient.set_keep_alive` (the sync client `workers/
    tasks/deployment.py` uses to pin) rather than hand-rolling a second
    async HTTP call to the same endpoint. `set_keep_alive` is sync
    (`httpx.Client`, built for Celery's sync workers), so it's offloaded to a
    thread here to avoid blocking the event loop — same reasoning
    `api/core/auth.py::current_user_optional` documents for its own
    sync-call-from-async-code case.
    """
    settings = get_settings()
    try:
        client = OllamaClient(str(settings.ollama_base_url), timeout=_UNLOAD_TIMEOUT_SECONDS)
        await asyncio.to_thread(client.set_keep_alive, tag, 0)
    except Exception:
        log.warning("failed to unload ollama model %s", tag, exc_info=True)


async def _get_owned(db: AsyncSession, deployment_id: UUID, user: CurrentUser) -> Deployment:
    """404 if missing, 403 if it exists but belongs to someone else.

    A local, simpler cousin of `api/services/ownership.py`'s
    `assert_*_access` helpers: `Deployment.owner_id` is direct (no
    project/training hop to walk) and the router's `require_authenticated_user`
    guarantees `user` is never `None`, so there is no anonymous-caller branch
    to support here.
    """
    deployment = await db.get(Deployment, deployment_id)
    if deployment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deployment {deployment_id} not found",
        )
    if deployment.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Deployment {deployment_id} is not accessible",
        )
    return deployment


async def _model_tag_for(db: AsyncSession, deployment: Deployment) -> str | None:
    """The linked artifact's current Ollama tag, or `None` if there is none
    (never exported) or no longer one (artifact deleted, `ondelete=SET NULL`).
    """
    if deployment.model_artifact_id is None:
        return None
    return (
        await db.execute(
            select(ModelArtifact.ollama_model_tag).where(
                ModelArtifact.id == deployment.model_artifact_id
            )
        )
    ).scalar_one_or_none()


def _to_response(deployment: Deployment, model_tag: str | None) -> DeploymentResponse:
    return DeploymentResponse(
        id=deployment.id,
        name=deployment.name,
        model_artifact_id=deployment.model_artifact_id,
        model_tag=model_tag,
        status=deployment.status,
        rate_limit_per_min=deployment.rate_limit_per_min,
        job_id=deployment.celery_task_id,
        error_message=deployment.error_message,
        created_at=deployment.created_at,
        updated_at=deployment.updated_at,
    )


__all__ = [
    "create_deployment",
    "delete_deployment",
    "get_deployment",
    "list_deployments",
    "stop_deployment",
    "stop_for_artifact",
    "update_deployment",
]
