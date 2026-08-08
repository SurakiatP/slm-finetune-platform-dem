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

from api.core.auth import CurrentUser, require_user
from api.core.database import get_db
from api.schemas.artifacts import (
    ModelArtifactResponse,
    ModelExportRequest,
    ModelExportResponse,
)
from api.schemas.download_links import ModelDownloadUrlResponse
from api.schemas.enums import ArtifactFormat
from api.schemas.responses import Page
from api.services.download_links import mint_model_download_url
from api.services.model_service import (
    cancel_export as _cancel_export,
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
    user: Annotated[CurrentUser | None, Depends(require_user)],
    project_id: Annotated[UUID | None, Query()] = None,
    training_job_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[ModelArtifactResponse]:
    return await _list_models(
        db,
        project_id=project_id,
        training_job_id=training_job_id,
        limit=limit,
        offset=offset,
        user=user,
    )


@router.get(
    "/{model_id}",
    response_model=ModelArtifactResponse,
    summary="Get a model artifact by id",
)
async def get_model(
    model_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
) -> ModelArtifactResponse:
    return await _get_model(db, model_id, user)


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
    user: Annotated[CurrentUser | None, Depends(require_user)],
) -> ModelExportResponse:
    return await submit_export_job(db, model_id=model_id, request=body, user=user)


@router.post(
    "/{model_id}/export/cancel",
    response_model=dict[str, str],
    summary="Cancel an in-progress model export (idempotent)",
)
async def cancel_export(
    model_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
) -> dict[str, str]:
    # Declared right after POST /{model_id}/export (a distinct 2-segment
    # path) and before GET /{model_id}/download — Starlette matches routes
    # by exact segment shape, so this 3-segment path is never shadowed by
    # either neighbour regardless of order, but keeping it here groups the
    # export lifecycle together for readers.
    return await _cancel_export(db, model_id, user)


@router.get(
    "/{model_id}/download",
    summary="Download a previously-exported artifact (file stream)",
    response_class=StreamingResponse,
)
async def download_model(
    model_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
    fmt: Annotated[str, Query(alias="format")] = "gguf",
) -> StreamingResponse:
    return await download_artifact(db, model_id=model_id, fmt=fmt, user=user)


@router.get(
    "/{model_id}/download-url",
    response_model=ModelDownloadUrlResponse,
    summary="Mint presigned MinIO URL(s) for a previously-exported artifact",
)
async def get_model_download_url(
    model_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
    fmt: Annotated[ArtifactFormat, Query(alias="format")] = ArtifactFormat.GGUF,
) -> ModelDownloadUrlResponse:
    # Additive alongside `GET /{model_id}/download` (the existing streaming
    # endpoint stays) — this is the presigned-URL path that lets large
    # artifacts bypass the API process entirely, and the only path that
    # covers `safetensors`/`lora` (the streaming endpoint 400s on those; see
    # `download_artifact`'s multi-file-directory branch).
    return await mint_model_download_url(db, model_id, fmt, user)
