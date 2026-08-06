"""Application service for the `/api/v1/models` REST resource.

This is the resource for trained-model artifacts (one per successful TrainingJob).
Endpoints:
  • `GET    /models`               — list (paginated, optional project filter)
  • `GET    /models/{id}`          — detail
  • `POST   /models/{id}/export`   — enqueue GGUF / SafeTensors export
  • `GET    /models/{id}/download` — stream a previously-exported artifact
"""

from __future__ import annotations

from typing import AsyncIterator
from uuid import UUID

from fastapi import HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.auth import CurrentUser
from api.models.model_artifact import ModelArtifact
from api.models.training_job import TrainingJob
from api.schemas.artifacts import (
    ModelArtifactResponse,
    ModelExportRequest,
    ModelExportResponse,
)
from api.schemas.enums import ArtifactFormat, JobStatus
from api.schemas.responses import Page
from api.services import ownership
from api.services.job_control import TERMINAL_JOB_STATUSES, revoke_celery_task
from workers.storage import get_minio_client, parse_s3_uri


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
    return ModelArtifactResponse.model_validate(artifact)


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

    404 if the artifact doesn't exist or belongs to another user. 409 if no
    export was ever requested for this artifact (``export_status is None``)
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
    await db.commit()
    return {"artifact_id": str(artifact.id), "status": JobStatus.CANCELLED.value}


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
    # server-side (RAM cost on large weights). Expose the URI for the client
    # to enumerate via the MinIO API or our object-listing endpoint.
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            f"{fmt_lower} export is a multi-file directory (uri={uri}); "
            "fetch individual objects via the MinIO API."
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
    "download_artifact",
]
