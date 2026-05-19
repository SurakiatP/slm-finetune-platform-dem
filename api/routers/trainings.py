"""Trainings router — start manual / HPO runs, list, cancel, get MLflow URL."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import get_db
from api.schemas.enums import JobStatus, TrainingMode
from api.schemas.responses import Page
from api.schemas.training import (
    HPOTrainingRequest,
    ManualTrainingRequest,
    TrainingJobAcceptedResponse,
    TrainingRequest,
)
from api.schemas.trainings import (
    MlflowUrlResponse,
    TrainingLossHistoryResponse,
    TrainingMetricsResponse,
    TrainingResponse,
)
from api.services import trainings_service
from api.services.training_service import (
    submit_hpo_training_job,
    submit_manual_training_job,
)

router = APIRouter()


@router.post(
    "",
    response_model=TrainingJobAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a training job (manual or HPO)",
)
async def start_training(
    body: TrainingRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TrainingJobAcceptedResponse:
    if body.mode is TrainingMode.MANUAL:
        assert isinstance(body, ManualTrainingRequest)
        return await submit_manual_training_job(db, body)
    assert isinstance(body, HPOTrainingRequest)
    return await submit_hpo_training_job(db, body)


@router.get(
    "",
    response_model=Page[TrainingResponse],
    summary="List training jobs (filter by project / status)",
)
async def list_trainings(
    db: Annotated[AsyncSession, Depends(get_db)],
    project_id: Annotated[UUID | None, Query()] = None,
    status_filter: Annotated[JobStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[TrainingResponse]:
    return await trainings_service.list_trainings(
        db,
        project_id=project_id,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{training_id}",
    response_model=TrainingResponse,
    summary="Get a training job by id",
)
async def get_training(
    training_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TrainingResponse:
    return await trainings_service.get_training(db, training_id)


@router.delete(
    "/{training_id}",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Cancel a running / pending training job",
)
async def cancel_training(
    training_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str]:
    return await trainings_service.cancel_training(db, training_id)


@router.get(
    "/{training_id}/mlflow-url",
    response_model=MlflowUrlResponse,
    summary="Resolve the MLflow run URL for a training job",
)
async def get_mlflow_url(
    training_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MlflowUrlResponse:
    return await trainings_service.get_mlflow_url(db, training_id)


@router.get(
    "/{training_id}/metrics",
    response_model=TrainingMetricsResponse,
    summary="Full metric history (all keys) + HPO child summary",
)
async def get_training_metrics(
    training_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TrainingMetricsResponse:
    return await trainings_service.get_training_metrics(db, training_id)


@router.get(
    "/{training_id}/loss-history",
    response_model=TrainingLossHistoryResponse,
    summary="Lightweight train_loss + eval_loss series for chart components",
)
async def get_training_loss_history(
    training_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TrainingLossHistoryResponse:
    return await trainings_service.get_training_loss_history(db, training_id)
