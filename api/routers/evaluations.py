"""Evaluations router — run + compare task-specific metrics + LLM judge."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.auth import CurrentUser, require_user
from api.core.database import get_db
from api.schemas.enums import JobStatus
from api.schemas.evaluations import (
    EvaluationAcceptedResponse,
    EvaluationCompareRequest,
    EvaluationCompareResponse,
    EvaluationCreate,
    EvaluationResponse,
)
from api.schemas.responses import Page
from api.services import evaluation_service

router = APIRouter()


@router.post(
    "",
    response_model=EvaluationAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an evaluation run",
)
async def start_evaluation(
    body: EvaluationCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
) -> EvaluationAcceptedResponse:
    return await evaluation_service.submit_evaluation_job(db, body, user)


@router.get(
    "",
    response_model=Page[EvaluationResponse],
    summary="List evaluation runs (filter by model / dataset / status)",
)
async def list_evaluations(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
    model_artifact_id: Annotated[UUID | None, Query()] = None,
    dataset_id: Annotated[UUID | None, Query()] = None,
    status_filter: Annotated[JobStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[EvaluationResponse]:
    return await evaluation_service.list_evaluations(
        db,
        model_artifact_id=model_artifact_id,
        dataset_id=dataset_id,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
        user=user,
    )


@router.get(
    "/{evaluation_id}",
    response_model=EvaluationResponse,
    summary="Get an evaluation run by id",
)
async def get_evaluation(
    evaluation_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
) -> EvaluationResponse:
    return await evaluation_service.get_evaluation(db, evaluation_id, user)


@router.post(
    "/{evaluation_id}/cancel",
    response_model=dict[str, str],
    summary="Cancel a running evaluation (idempotent)",
)
async def cancel_evaluation(
    evaluation_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
) -> dict[str, str]:
    return await evaluation_service.cancel_evaluation(db, evaluation_id, user)


@router.post(
    "/compare",
    response_model=EvaluationCompareResponse,
    summary="Compare metrics across multiple evaluation runs",
)
async def compare_evaluations(
    body: EvaluationCompareRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
) -> EvaluationCompareResponse:
    return await evaluation_service.compare_evaluations(db, body, user)
