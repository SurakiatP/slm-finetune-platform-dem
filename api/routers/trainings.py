"""Trainings router — start manual / HPO runs, list, cancel, get MLflow URL."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.auth import CurrentUser, require_user
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
from api.services import idempotency, ownership, trainings_service
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
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
) -> TrainingJobAcceptedResponse | JSONResponse:
    body_json = body.model_dump(mode="json")
    if (replayed := await idempotency.replay(request, user, body_json)) is not None:
        return replayed
    # Parent-check here rather than inside training_service.submit_*_job:
    # that module is owned by another workstream on this branch and out of
    # scope for this change. Both submit functions already 400 when
    # dataset.project_id != project.id, so asserting ownership of
    # body.project_id alone also gates body.dataset_id transitively — a
    # caller can't train against someone else's dataset without also
    # naming that someone else's project, which this call already blocks.
    await ownership.assert_project_access(db, body.project_id, user)
    if body.mode is TrainingMode.MANUAL:
        assert isinstance(body, ManualTrainingRequest)
        resp = await submit_manual_training_job(db, body)
    else:
        assert isinstance(body, HPOTrainingRequest)
        resp = await submit_hpo_training_job(db, body)
    await idempotency.remember(request, user, body_json, resp.model_dump(mode="json"))
    return resp


@router.get(
    "",
    response_model=Page[TrainingResponse],
    summary="List training jobs (filter by project / status)",
)
async def list_trainings(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
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
        user=user,
    )


@router.get(
    "/{training_id}",
    response_model=TrainingResponse,
    summary="Get a training job by id",
)
async def get_training(
    training_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
) -> TrainingResponse:
    return await trainings_service.get_training(db, training_id, user)


@router.delete(
    "/{training_id}",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Cancel a running / pending training job",
)
async def cancel_training(
    training_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
) -> dict[str, str]:
    return await trainings_service.cancel_training(db, training_id, user)


@router.post(
    "/{training_id}/cancel",
    response_model=dict[str, str],
    summary="Cancel a running / pending training job (POST alias for DELETE)",
)
async def cancel_training_post(
    training_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
) -> dict[str, str]:
    """Same idempotent cancel semantics as `DELETE /{training_id}`.

    Delegates to the exact same service function — no duplicated logic —
    so both verbs always agree on behaviour.
    """
    return await trainings_service.cancel_training(db, training_id, user)


@router.get(
    "/{training_id}/mlflow-url",
    response_model=MlflowUrlResponse,
    summary="Resolve the MLflow run URL for a training job",
)
async def get_mlflow_url(
    training_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
) -> MlflowUrlResponse:
    return await trainings_service.get_mlflow_url(db, training_id, user)


@router.get(
    "/{training_id}/metrics",
    response_model=TrainingMetricsResponse,
    summary="Full metric history (all keys) + HPO child summary",
)
async def get_training_metrics(
    training_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
) -> TrainingMetricsResponse:
    return await trainings_service.get_training_metrics(db, training_id, user)


@router.get(
    "/{training_id}/loss-history",
    response_model=TrainingLossHistoryResponse,
    summary="Lightweight train_loss + eval_loss series for chart components",
)
async def get_training_loss_history(
    training_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
) -> TrainingLossHistoryResponse:
    return await trainings_service.get_training_loss_history(db, training_id, user)
