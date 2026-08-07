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
    Request,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.auth import CurrentUser, require_user
from api.core.database import get_db
from api.schemas.datasets import DatasetPreviewResponse, DatasetResponse
from api.schemas.download_links import DatasetDownloadUrlResponse
from api.schemas.enums import TaskType
from api.schemas.responses import Page
from api.schemas.sdg import (
    SDGJobAcceptedResponse,
    SDGRequest,
    SDGRequestDescriptionOnly,
    SDGRequestWithSeed,
    SeedUploadResponse,
)
from api.services import datasets_service, idempotency, ownership
from api.services.download_links import mint_dataset_download_url
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
    user: Annotated[CurrentUser | None, Depends(require_user)],
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
        user=user,
    )


@router.post(
    "/generate",
    response_model=SDGJobAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Generate a synthetic dataset via OpenRouter",
)
async def generate_dataset(
    body: SDGRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
) -> SDGJobAcceptedResponse | JSONResponse:
    assert isinstance(body, (SDGRequestWithSeed, SDGRequestDescriptionOnly))
    body_json = body.model_dump(mode="json")
    if (replayed := await idempotency.replay(request, user, body_json)) is not None:
        return replayed
    # Parent-check here rather than inside sdg_service.submit_sdg_job: that
    # module is owned by another workstream on this branch and out of scope
    # for this change. submit_sdg_job's own validation already rejects a
    # `seed_dataset_id` whose project doesn't match `body.project_id` (400),
    # so asserting ownership of the project alone is sufficient to also gate
    # the seed dataset transitively — a caller can't point `with_seed` mode
    # at someone else's seed without also naming that someone else's
    # project, which this call already blocks.
    await ownership.assert_project_access(db, body.project_id, user)
    resp = await submit_sdg_job(db, body)
    await idempotency.remember(request, user, body_json, resp.model_dump(mode="json"))
    return resp


@router.get(
    "",
    response_model=Page[DatasetResponse],
    summary="List datasets (optionally filter by project)",
)
async def list_datasets(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
    project_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[DatasetResponse]:
    return await datasets_service.list_datasets(
        db, project_id=project_id, limit=limit, offset=offset, user=user
    )


@router.get(
    "/{dataset_id}",
    response_model=DatasetResponse,
    summary="Get a dataset by id",
)
async def get_dataset(
    dataset_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
) -> DatasetResponse:
    return await datasets_service.get_dataset(db, dataset_id, user)


@router.get(
    "/{dataset_id}/preview",
    response_model=DatasetPreviewResponse,
    summary="Preview the first N rows of a dataset",
)
async def preview_dataset(
    dataset_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> DatasetPreviewResponse:
    return await datasets_service.preview_dataset(db, dataset_id, limit, user)


@router.get(
    "/{dataset_id}/download",
    summary="Download the raw dataset file (JSONL)",
    response_class=StreamingResponse,
)
async def download_dataset(
    dataset_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
) -> StreamingResponse:
    return await datasets_service.download_dataset(db, dataset_id, user)


@router.get(
    "/{dataset_id}/download-url",
    response_model=DatasetDownloadUrlResponse,
    summary="Mint a presigned MinIO URL for the dataset's stored object",
)
async def get_dataset_download_url(
    dataset_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
) -> DatasetDownloadUrlResponse:
    # Additive alongside `GET /{dataset_id}/download` (the existing
    # streaming endpoint stays). Falls back to the seed PDF object when
    # `storage_uri` is null — see `mint_dataset_download_url`'s docstring —
    # which also closes the "PDF-seeded dataset has no download surface"
    # gap the streaming endpoint has today.
    return await mint_dataset_download_url(db, dataset_id, user)


@router.delete(
    "/{dataset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a dataset",
)
async def delete_dataset(
    dataset_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
) -> None:
    await datasets_service.delete_dataset(db, dataset_id, user)


@router.post(
    "/{dataset_id}/cancel",
    response_model=dict[str, str],
    summary="Cancel a running SDG generation job (idempotent)",
)
async def cancel_dataset(
    dataset_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
) -> dict[str, str]:
    return await datasets_service.cancel_dataset(db, dataset_id, user)
