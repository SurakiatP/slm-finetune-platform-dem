"""Models router — list trained-model artifacts, export, download.

Note: the file is named `models.py` for REST-resource clarity. The SQLAlchemy
ORM package is `api.models` — they don't collide because of namespacing.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import get_db
from api.schemas.artifacts import (
    ModelArtifactResponse,
    ModelExportRequest,
    ModelExportResponse,
)
from api.schemas.responses import Page
from api.services.model_service import (
    download_artifact,
    get_model as _get_model,
    list_models as _list_models,
    submit_export_job,
)

router = APIRouter()


@router.get(
    "",
    response_model=Page[ModelArtifactResponse],
    summary="List trained model artifacts",
)
async def list_models(
    db: Annotated[AsyncSession, Depends(get_db)],
    project_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[ModelArtifactResponse]:
    return await _list_models(db, project_id=project_id, limit=limit, offset=offset)


@router.get(
    "/{model_id}",
    response_model=ModelArtifactResponse,
    summary="Get a model artifact by id",
)
async def get_model(
    model_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ModelArtifactResponse:
    return await _get_model(db, model_id)


@router.post(
    "/{model_id}/export",
    response_model=ModelExportResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Export a model as GGUF or SafeTensors",
)
async def export_model(
    model_id: UUID,
    body: ModelExportRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ModelExportResponse:
    return await submit_export_job(db, model_id=model_id, request=body)


@router.get(
    "/{model_id}/download",
    summary="Download a previously-exported artifact (file stream)",
    response_class=StreamingResponse,
)
async def download_model(
    model_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    fmt: Annotated[str, Query(alias="format")] = "gguf",
) -> StreamingResponse:
    return await download_artifact(db, model_id=model_id, fmt=fmt)
