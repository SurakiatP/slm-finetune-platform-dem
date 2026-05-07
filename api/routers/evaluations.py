"""Evaluations router — run + compare task-specific metrics + LLM judge."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import get_db
from api.schemas.evaluations import (
    EvaluationAcceptedResponse,
    EvaluationCompareRequest,
    EvaluationCompareResponse,
    EvaluationCreate,
    EvaluationResponse,
)
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
) -> EvaluationAcceptedResponse:
    return await evaluation_service.submit_evaluation_job(db, body)


@router.get(
    "/{evaluation_id}",
    response_model=EvaluationResponse,
    summary="Get an evaluation run by id",
)
async def get_evaluation(
    evaluation_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> EvaluationResponse:
    return await evaluation_service.get_evaluation(db, evaluation_id)


@router.post(
    "/compare",
    response_model=EvaluationCompareResponse,
    summary="Compare metrics across multiple evaluation runs",
)
async def compare_evaluations(
    body: EvaluationCompareRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> EvaluationCompareResponse:
    return await evaluation_service.compare_evaluations(db, body)
