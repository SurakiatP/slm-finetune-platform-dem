"""Application service for the `/api/v1/models` REST resource.

This is the resource for trained-model artifacts (one per successful TrainingJob).
Endpoints:
  • `GET    /models`               — list (paginated, optional project filter)
  • `GET    /models/{id}`          — detail
  • `POST   /models/{id}/export`   — enqueue GGUF / SafeTensors export
  • `GET    /models/{id}/download` — stream a previously-exported artifact
"""

from __future__ import annotations

import logging
from typing import AsyncIterator
from uuid import UUID

from fastapi import HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core import request_context
from api.core.auth import CurrentUser
from api.core.config import get_settings
from api.models.evaluation_run import EvaluationRun
from api.models.model_artifact import ModelArtifact
from api.models.training_job import TrainingJob
from api.schemas.artifacts import (
    ModelArtifactResponse,
    ModelExportRequest,
    ModelExportResponse,
)
from api.schemas.enums import ArtifactFormat, JobStatus
from api.schemas.responses import Page
from api.services import audit_service, ownership, quota, queue_position
from api.services.quota import Bucket
from api.services.job_control import TERMINAL_JOB_STATUSES, revoke_celery_task
from workers.ollama_client import OllamaClient
from workers.storage import get_minio_client, parse_s3_uri, remove_prefix

log = logging.getLogger(__name__)


# ---- list / get -----------------------------------------------------------


async def list_models(
    db: AsyncSession,
    *,
    project_id: UUID | None,
    training_job_id: UUID | None,
    limit: int,
    offset: int,
    user: CurrentUser | None = None,
) -> Page[ModelArtifactResponse]:
    """List artifacts, optionally filtered by parent project or training job."""
    base = select(ModelArtifact).order_by(ModelArtifact.created_at.desc())
    count = select(func.count()).select_from(ModelArtifact)
    if project_id is not None:
        # A `.in_(subquery)` rather than a `.join(TrainingJob, ...)` here so
        # this filter can never collide with the join
        # `ownership.scope_models_to_owner` adds below when `user` is set —
        # joining the same target table twice on the same statement is a
        # SQL error, not a silent merge.
        owning_jobs = select(TrainingJob.id).where(TrainingJob.project_id == project_id)
        base = base.where(ModelArtifact.training_job_id.in_(owning_jobs))
        count = count.where(ModelArtifact.training_job_id.in_(owning_jobs))
    if training_job_id is not None:
        base = base.where(ModelArtifact.training_job_id == training_job_id)
        count = count.where(ModelArtifact.training_job_id == training_job_id)
    base = ownership.scope_models_to_owner(base, user)
    count = ownership.scope_models_to_owner(count, user)

    total = (await db.execute(count)).scalar_one()
    rows = (await db.execute(base.limit(limit).offset(offset))).scalars().all()
    return Page[ModelArtifactResponse](
        items=[ModelArtifactResponse.model_validate(r) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


async def get_model(
    db: AsyncSession, model_id: UUID, user: CurrentUser | None = None
) -> ModelArtifactResponse:
    artifact = await ownership.assert_model_access(db, model_id, user)
    response = ModelArtifactResponse.model_validate(artifact)
    # Display-only, in-flight-only: only a PENDING/RUNNING export can
    # meaningfully sit in a GPU queue, so skip the lookup entirely otherwise
    # (and never populate on list_models — see queue_position.py).
    if artifact.export_status in (JobStatus.PENDING, JobStatus.RUNNING):
        project_id = await _project_id_for_artifact(db, artifact)
        info = (
            await queue_position.project_queue_info(db, project_id)
            if project_id is not None
            else None
        )
        if info is not None:
            response = response.model_copy(
                update={
                    "queue_state": info.queue_state,
                    "queue_position": info.queue_position,
                    "owner_queue_position": info.owner_queue_position,
                }
            )
    return response


# ---- export ---------------------------------------------------------------


async def submit_export_job(
    db: AsyncSession,
    *,
    model_id: UUID,
    request: ModelExportRequest,
    user: CurrentUser | None = None,
) -> ModelExportResponse:
    """Validate and enqueue a `model.export` Celery task.

    Ownership check first: exporting someone else's artifact (spending GPU
    time on their weights, or getting back a download link to them) is the
    attack that matters here, more than reading the artifact metadata is.
    """
    artifact = await ownership.assert_model_access(db, model_id, user)
    if not artifact.lora_adapter_uri:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Model {model_id} has no LoRA adapter on file — "
                "training likely never completed"
            ),
        )

    # One artifact, one export at a time. Without this guard a second POST
    # while an export is in flight overwrote `export_celery_task_id` below,
    # which orphaned the first Celery task: nothing held its id any more, so
    # `POST /export/cancel` could never revoke it and the progress frames it
    # kept publishing to `job:{old_id}` went to a channel no client was on.
    # A terminal `export_status` (completed/failed/cancelled) is deliberately
    # NOT blocked — re-exporting a finished artifact is a normal thing to do.
    if artifact.export_status in (JobStatus.PENDING, JobStatus.RUNNING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Model {model_id} already has an export in flight "
                f"(job {artifact.export_celery_task_id}); "
                f"POST /api/v1/models/{model_id}/export/cancel first."
            ),
        )

    # GPU quota gate — one bucket shared with training/HPO and evaluation
    # (see quota.py). Deliberately runs AFTER the in-flight 409 guard above,
    # not before: a caller re-POSTing an export that is already running
    # deserves the specific, actionable 409 ("it's already running, cancel
    # it first"), not a generic 429 that tells them nothing about what to
    # do. The two guards never conflict — they answer different questions
    # (is *this* artifact mid-export? vs. is this actor/the platform over
    # its GPU concurrency cap?), so ordering them this way costs nothing.
    await quota.assert_can_submit(db, bucket=Bucket.GPU, actor_id=request_context.current_user_id())

    # Local import keeps the API process from eagerly loading worker-only deps.
    from workers.tasks.model_export import export_model

    async_result = export_model.apply_async(
        kwargs={
            "artifact_id": str(artifact.id),
            "format": request.format.value,
            "quantization": request.quantization,
        },
    )
    job_id: str = async_result.id

    # Persist job-control state so an in-flight export can be recovered or
    # cancelled by job id after a reload (mirrors sdg_service.submit_sdg_job's
    # enqueue -> persist -> commit -> return ordering).
    artifact.export_celery_task_id = job_id
    artifact.export_status = JobStatus.PENDING
    audit_service.record(
        db,
        action="export.submit",
        resource_type="model",
        resource_id=str(artifact.id),
        project_id=await _project_id_for_artifact(db, artifact),
        actor_id=request_context.current_user_id(),
        request_id=request_context.current_request_id(),
        metadata={"job_id": job_id, "format": request.format.value, "quantization": request.quantization},
    )
    await db.commit()

    return ModelExportResponse(
        artifact_id=artifact.id,
        format=request.format,
        job_id=job_id,
        status=JobStatus.PENDING,
        websocket_url=f"/ws/jobs/{job_id}",
    )


async def cancel_export(
    db: AsyncSession, model_id: UUID, user: CurrentUser | None = None
) -> dict[str, str]:
    """Revoke the underlying export Celery task + flip export_status to CANCELLED.

    404 if the artifact doesn't exist; 403 if it exists but belongs to
    another user (ADR-012). 409 if no export was ever requested for this
    artifact (``export_status is None``)
    — there is nothing to cancel, and pretending otherwise would report a
    fake CANCELLED transition for a job that was never enqueued. Idempotent
    once export_status is already terminal: returns 200 with the current
    status, no revoke.
    """
    artifact = await ownership.assert_model_access(db, model_id, user)
    if artifact.export_status is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Model {model_id} has no export in progress — "
                f"POST /api/v1/models/{model_id}/export first."
            ),
        )
    if artifact.export_status in TERMINAL_JOB_STATUSES:
        return {"artifact_id": str(artifact.id), "status": artifact.export_status.value}

    revoke_celery_task(artifact.export_celery_task_id, context=f"model export {model_id}")

    artifact.export_status = JobStatus.CANCELLED
    audit_service.record(
        db,
        action="export.cancel",
        resource_type="model",
        resource_id=str(artifact.id),
        project_id=await _project_id_for_artifact(db, artifact),
        actor_id=request_context.current_user_id(),
        request_id=request_context.current_request_id(),
        metadata={"job_id": artifact.export_celery_task_id},
    )
    await db.commit()
    return {"artifact_id": str(artifact.id), "status": JobStatus.CANCELLED.value}


# ---- delete -----------------------------------------------------------------


async def purge_artifact(
    db: AsyncSession,
    artifact: ModelArtifact,
    *,
    context: str | None = None,
) -> None:
    """Best-effort purge of `artifact`'s external state, then `db.delete()` it.

    PUBLIC + REUSABLE: exported for a later task to import directly (e.g. a
    training-job/project cascade cleanup, or a retention-policy purge job) —
    not just `delete_model` below. Ownership/authorization is entirely the
    CALLER's responsibility; this helper performs no access check of its own.

    Order: MinIO objects (gguf/safetensors/lora, each a key *prefix* — see
    `download_artifact`'s multi-file-directory handling above) -> Ollama tag
    -> dependent-row cleanup -> ORM delete. The two external-system steps are
    log-and-continue best-effort (mirrors `datasets_service.delete_dataset`'s
    MinIO cleanup and `revoke_celery_task`'s "broker hiccup must not block
    the DB update" reasoning) so a storage or Ollama daemon hiccup never
    leaves the DB row behind — the row is the source of truth, not the
    external copies.

    Dependent-row cleanup, NOT best-effort: `EvaluationRun.model_artifact_id`
    is `ForeignKey(..., ondelete="CASCADE")` but that column is also
    `nullable=False`, and `ModelArtifact.evaluation_runs` carries no
    `passive_deletes` setting — so a plain `await db.delete(artifact)` makes
    SQLAlchemy's unit-of-work try to NULL that column out first (its default
    disassociate-before-delete behavior for a non-cascading collection),
    which sqlite AND Postgres both reject as a NOT NULL violation, on every
    backend, every time. Deleting the dependent `EvaluationRun` rows
    ourselves first — via a Core statement so the ORM never attempts to load
    or nullify them — sidesteps that entirely and leaves the *intent* of the
    DB's `ondelete="CASCADE"` satisfied (evaluations for a deleted model
    don't outlive it) without touching `api/models/model_artifact.py`.

    `context` is a `revoke_celery_task`-style free-text label for log lines
    (defaults to `f"model {artifact.id}"`).

    MUST NOT commit — the caller owns the transaction boundary, exactly like
    `db.delete(ds)` in `datasets_service.delete_dataset`.
    """
    log_ctx = context or f"model {artifact.id}"

    minio = get_minio_client()
    for uri in (artifact.gguf_uri, artifact.safetensors_uri, artifact.lora_adapter_uri):
        if not uri:
            continue
        try:
            bucket, prefix = parse_s3_uri(uri)
            remove_prefix(minio, bucket, prefix)
        except Exception:  # noqa: BLE001 — best-effort, never block the DB delete
            log.warning("failed to remove storage for %s (%s)", log_ctx, uri, exc_info=True)

    if artifact.ollama_model_tag:
        try:
            OllamaClient(str(get_settings().ollama_base_url)).delete_model(
                artifact.ollama_model_tag
            )
        except Exception:  # noqa: BLE001 — tolerate already-gone/unreachable daemon
            log.warning(
                "failed to delete ollama tag for %s (%s)",
                log_ctx,
                artifact.ollama_model_tag,
                exc_info=True,
            )

    await db.execute(delete(EvaluationRun).where(EvaluationRun.model_artifact_id == artifact.id))
    await db.delete(artifact)


async def delete_model(
    db: AsyncSession, model_id: UUID, user: CurrentUser | None = None
) -> None:
    """Hard-delete a model artifact: purge its external state, then the row.

    409 while an export is in flight (PENDING/RUNNING) — same signal and
    wording style as `submit_export_job`'s in-flight guard, naming the job so
    the caller knows to `POST /export/cancel` first rather than deleting out
    from under a running Celery task. A terminal `export_status` (or `None`,
    i.e. never exported) does not block deletion, mirroring the "terminal is
    fine" rule `cancel_export` already documents.

    Cascade note: `EvaluationRun.model_artifact_id` is
    `ForeignKey(..., ondelete="CASCADE")`, so any evaluation runs against
    this artifact are removed by the database when the row is deleted —
    no application-level cleanup needed for that FK.
    """
    artifact = await ownership.assert_model_access(db, model_id, user)
    if artifact.export_status in (JobStatus.PENDING, JobStatus.RUNNING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Model {model_id} has an export in flight "
                f"(job {artifact.export_celery_task_id}); "
                f"POST /api/v1/models/{model_id}/export/cancel first."
            ),
        )

    audit_service.record(
        db,
        action="model.delete",
        resource_type="model",
        resource_id=str(artifact.id),
        project_id=await _project_id_for_artifact(db, artifact),
        actor_id=request_context.current_user_id(),
        request_id=request_context.current_request_id(),
        metadata={"name": artifact.name, "base_model": artifact.base_model},
    )
    await purge_artifact(db, artifact, context=f"model delete {model_id}")
    await db.commit()


async def _project_id_for_artifact(db: AsyncSession, artifact: ModelArtifact):
    """Walk ModelArtifact -> TrainingJob -> Project (2 hops).

    Mirrors the depth `api/services/job_ownership.py` documents. Returns None
    if the link is missing rather than raising — an audit row with a null
    project is still worth keeping, and every caller has already passed its
    own ownership check by this point.
    """
    training_job = await db.get(TrainingJob, artifact.training_job_id)
    return training_job.project_id if training_job is not None else None


# ---- download -------------------------------------------------------------


async def download_artifact(
    db: AsyncSession,
    *,
    model_id: UUID,
    fmt: str,
    user: CurrentUser | None = None,
) -> StreamingResponse:
    """Stream a previously-exported artifact file out of MinIO.

    For GGUF, returns the first `.gguf` blob found under the export prefix.
    For SafeTensors, the merged-weights tarball is not zipped server-side —
    we expose the raw object listing path. Callers can download individual
    files via MinIO directly using the URI on the artifact record.
    """
    artifact = await ownership.assert_model_access(db, model_id, user)

    fmt_lower = fmt.lower()
    if fmt_lower == ArtifactFormat.GGUF.value:
        uri = artifact.gguf_uri
    elif fmt_lower == ArtifactFormat.SAFETENSORS.value:
        uri = artifact.safetensors_uri
    elif fmt_lower == ArtifactFormat.LORA.value:
        uri = artifact.lora_adapter_uri
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported format: {fmt}",
        )

    if not uri:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Model {model_id} has not been exported as {fmt_lower}. "
                f"POST /api/v1/models/{model_id}/export first."
            ),
        )

    # Model weights leaving the system. Same reasoning as the dataset
    # download: nothing here mutates, but "who took a copy of which model,
    # and when" is precisely what an audit log is for — so it gets its own
    # commit, before the stream opens.
    audit_service.record(
        db,
        action="model.download",
        resource_type="model",
        resource_id=str(artifact.id),
        project_id=await _project_id_for_artifact(db, artifact),
        actor_id=request_context.current_user_id(),
        request_id=request_context.current_request_id(),
        metadata={"format": fmt_lower, "uri": uri},
    )
    await db.commit()

    bucket, prefix = parse_s3_uri(uri)
    if fmt_lower == ArtifactFormat.GGUF.value:
        # Pick the first .gguf object under the prefix.
        target = _first_gguf_object(bucket, prefix)
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"No GGUF file found at {uri}",
            )
        return _stream_object(
            bucket=bucket,
            object_name=target,
            filename=f"{artifact.name}.gguf",
            media_type="application/octet-stream",
        )

    # SafeTensors / LoRA — these are multi-file directories. We don't tar/zip
    # server-side (RAM cost on large weights). Point the caller at the
    # presigned multi-file listing endpoint instead of echoing the raw
    # `s3://` URI back in the response: that URI is an internal storage
    # address (bucket + key layout), not something a client should ever see
    # or need — leaking it here was Wave 1b's item #4 fix. No URI in this
    # detail message, on purpose.
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            f"{fmt_lower} export is a multi-file directory; use "
            f"GET /api/v1/models/{model_id}/download-url?format={fmt_lower} "
            "to get a presigned URL for each file."
        ),
    )


# ---- streaming helpers ----------------------------------------------------


def _first_gguf_object(bucket: str, prefix: str) -> str | None:
    minio = get_minio_client()
    prefix = prefix.rstrip("/") + "/"
    for obj in minio.list_objects(bucket_name=bucket, prefix=prefix, recursive=True):
        if obj.object_name.lower().endswith(".gguf"):
            return obj.object_name
    return None


def _stream_object(
    *,
    bucket: str,
    object_name: str,
    filename: str,
    media_type: str,
) -> StreamingResponse:
    """Build a `StreamingResponse` over an in-flight MinIO `get_object`."""

    async def _iter() -> AsyncIterator[bytes]:
        # MinIO is sync; we stream chunks one at a time. fastapi's StreamingResponse
        # can take a sync generator too, but using the async wrapper is forward-compat.
        minio = get_minio_client()
        response = minio.get_object(bucket_name=bucket, object_name=object_name)
        try:
            for chunk in response.stream(64 * 1024):
                yield chunk
        finally:
            response.close()
            response.release_conn()

    return StreamingResponse(
        _iter(),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


__all__ = [
    "list_models",
    "get_model",
    "submit_export_job",
    "cancel_export",
    "purge_artifact",
    "delete_model",
    "download_artifact",
]
