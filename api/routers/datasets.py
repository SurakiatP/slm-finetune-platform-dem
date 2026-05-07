"""Datasets router — seed upload, SDG generation, preview, download."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import get_db
from api.schemas.datasets import DatasetPreviewResponse, DatasetResponse
from api.schemas.enums import TaskType
from api.schemas.responses import Page
from api.schemas.sdg import (
    SDGJobAcceptedResponse,
    SDGRequest,
    SDGRequestDescriptionOnly,
    SDGRequestWithSeed,
    SeedUploadResponse,
)
from api.services import datasets_service
from api.services.sdg_service import submit_sdg_job

router = APIRouter()


@router.post(
    "/upload-seed",
    response_model=SeedUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload seed examples (JSONL or JSON)",
)
async def upload_seed_dataset(
    db: Annotated[AsyncSession, Depends(get_db)],
    project_id: Annotated[UUID, Form(...)],
    task_type: Annotated[TaskType, Form(...)],
    file: Annotated[UploadFile, File(...)],
    name: Annotated[str | None, Form()] = None,
) -> SeedUploadResponse:
    return await datasets_service.upload_seed_dataset(
        db,
        project_id=project_id,
        task_type=task_type,
        name=name,
        file=file,
    )


@router.post(
    "/generate",
    response_model=SDGJobAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Generate a synthetic dataset via OpenRouter",
)
async def generate_dataset(
    body: SDGRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SDGJobAcceptedResponse:
    assert isinstance(body, (SDGRequestWithSeed, SDGRequestDescriptionOnly))
    return await submit_sdg_job(db, body)


@router.get(
    "",
    response_model=Page[DatasetResponse],
    summary="List datasets (optionally filter by project)",
)
async def list_datasets(
    db: Annotated[AsyncSession, Depends(get_db)],
    project_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[DatasetResponse]:
    return await datasets_service.list_datasets(
        db, project_id=project_id, limit=limit, offset=offset
    )


@router.get(
    "/{dataset_id}",
    response_model=DatasetResponse,
    summary="Get a dataset by id",
)
async def get_dataset(
    dataset_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DatasetResponse:
    return await datasets_service.get_dataset(db, dataset_id)


@router.get(
    "/{dataset_id}/preview",
    response_model=DatasetPreviewResponse,
    summary="Preview the first N rows of a dataset",
)
async def preview_dataset(
    dataset_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> DatasetPreviewResponse:
    return await datasets_service.preview_dataset(db, dataset_id, limit)


@router.get(
    "/{dataset_id}/download",
    summary="Download the raw dataset file (JSONL)",
    response_class=StreamingResponse,
)
async def download_dataset(
    dataset_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    return await datasets_service.download_dataset(db, dataset_id)


@router.delete(
    "/{dataset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a dataset",
)
async def delete_dataset(
    dataset_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    await datasets_service.delete_dataset(db, dataset_id)
