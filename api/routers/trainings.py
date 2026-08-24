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
    # Project and dataset ownership are two independent gates, not one
    # transitive check: by user decision (G3), a dataset from a different
    # project (or an orphaned dataset whose project was deleted) is a valid
    # training input as long as the caller owns *both* rows, so
    # `body.dataset_id` no longer rides along with the project check below —
    # `training_service.submit_*_job` asserts dataset ownership itself via
    # `ownership.assert_dataset_access`, given the same `user`.
    await ownership.assert_project_access(db, body.project_id, user)
    if body.mode is TrainingMode.MANUAL:
        assert isinstance(body, ManualTrainingRequest)
        resp = await submit_manual_training_job(db, body, user=user)
    else:
        assert isinstance(body, HPOTrainingRequest)
        resp = await submit_hpo_training_job(db, body, user=user)
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
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Hard-delete a finished training job (and its model artifact, if any)",
)
async def delete_training(
    training_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
) -> None:
    await trainings_service.delete_training(db, training_id, user)


@router.post(
    "/{training_id}/cancel",
    response_model=dict[str, str],
    summary="Cancel a running / pending training job",
)
async def cancel_training_post(
    training_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
) -> dict[str, str]:
    """Cancel an in-flight training job (idempotent on an already-terminal one).

    **BREAKING CHANGE note**: this used to be documented as a POST alias for
    `DELETE /{training_id}`, but `DELETE` was repurposed into a hard-delete
    (see that route above) — this is now the *only* cancel entry point for
    trainings. Callers that used to `DELETE` to cancel (e.g.
    `smart-model-tune`'s `engineApi.ts`) must switch to this endpoint.
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
