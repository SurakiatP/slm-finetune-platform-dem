"""Application service for `/api/v1/trainings` (read + cancel + mlflow-url).

The submission side (manual + HPO) lives in `training_service.py` (Phase 5/6).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings
from api.models.training_job import TrainingJob
from api.schemas.enums import JobStatus
from api.schemas.responses import Page
from api.schemas.trainings import MlflowUrlResponse, TrainingResponse

log = logging.getLogger(__name__)


async def list_trainings(
    db: AsyncSession,
    *,
    project_id: UUID | None,
    status_filter: JobStatus | None,
    limit: int,
    offset: int,
) -> Page[TrainingResponse]:
    base = select(TrainingJob).order_by(TrainingJob.created_at.desc())
    count = select(func.count()).select_from(TrainingJob)
    if project_id is not None:
        base = base.where(TrainingJob.project_id == project_id)
        count = count.where(TrainingJob.project_id == project_id)
    if status_filter is not None:
        base = base.where(TrainingJob.status == status_filter)
        count = count.where(TrainingJob.status == status_filter)
    total = (await db.execute(count)).scalar_one()
    rows = (await db.execute(base.limit(limit).offset(offset))).scalars().all()
    return Page[TrainingResponse](
        items=[TrainingResponse.model_validate(r) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


async def get_training(db: AsyncSession, training_id: UUID) -> TrainingResponse:
    job = await db.get(TrainingJob, training_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Training {training_id} not found",
        )
    return TrainingResponse.model_validate(job)


async def cancel_training(db: AsyncSession, training_id: UUID) -> dict[str, str]:
    """Revoke the underlying Celery task + flip status to CANCELLED.

    Idempotent: cancelling an already-terminal job returns 200 with the existing
    status. Cancelling a non-existent job returns 404.
    """
    job = await db.get(TrainingJob, training_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Training {training_id} not found",
        )
    if job.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
        return {"training_id": str(job.id), "status": job.status.value}

    if job.celery_task_id:
        try:
            # Local import — Celery is in base deps but this keeps test-only
            # imports tidy.
            from workers.celery_app import celery_app

            celery_app.control.revoke(job.celery_task_id, terminate=True, signal="SIGTERM")
        except Exception:  # noqa: BLE001 — proceed to flip status even on broker hiccup
            log.warning(
                "could not revoke celery task %s for training %s",
                job.celery_task_id,
                training_id,
                exc_info=True,
            )

    job.status = JobStatus.CANCELLED
    job.ended_at = datetime.now(timezone.utc)
    await db.commit()
    return {"training_id": str(job.id), "status": JobStatus.CANCELLED.value}


async def get_mlflow_url(db: AsyncSession, training_id: UUID) -> MlflowUrlResponse:
    job = await db.get(TrainingJob, training_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Training {training_id} not found",
        )
    settings = get_settings()
    url: str | None = None
    if job.mlflow_run_id and job.mlflow_experiment_id:
        base = str(settings.mlflow_tracking_uri).rstrip("/")
        url = f"{base}/#/experiments/{job.mlflow_experiment_id}/runs/{job.mlflow_run_id}"
    return MlflowUrlResponse(
        training_id=job.id,
        mlflow_run_id=job.mlflow_run_id,
        mlflow_url=url,
    )


__all__ = [
    "list_trainings",
    "get_training",
    "cancel_training",
    "get_mlflow_url",
]
