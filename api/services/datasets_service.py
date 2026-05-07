"""Application service for `/api/v1/datasets` (read + delete + upload-seed).

The SDG generation endpoint lives in `sdg_service.py` (Phase 4); this module
covers the rest of the dataset surface.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timezone
from typing import AsyncIterator
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings
from api.models.dataset import Dataset
from api.models.project import Project
from api.schemas.data_formats import parse_samples
from api.schemas.datasets import DatasetPreviewResponse, DatasetResponse
from api.schemas.enums import DatasetSource, TaskType
from api.schemas.responses import Page
from api.schemas.sdg import SeedUploadResponse
from workers.storage import (
    get_minio_client,
    parse_s3_uri,
    put_jsonl,
    s3_uri,
)

log = logging.getLogger(__name__)

# Cap on uploaded seed file size (MiB) — anything bigger almost certainly isn't seed data.
_MAX_SEED_BYTES = 10 * 1024 * 1024


# ---- read endpoints -------------------------------------------------------


async def list_datasets(
    db: AsyncSession,
    *,
    project_id: UUID | None,
    limit: int,
    offset: int,
) -> Page[DatasetResponse]:
    base = select(Dataset).order_by(Dataset.created_at.desc())
    count = select(func.count()).select_from(Dataset)
    if project_id is not None:
        base = base.where(Dataset.project_id == project_id)
        count = count.where(Dataset.project_id == project_id)
    total = (await db.execute(count)).scalar_one()
    rows = (await db.execute(base.limit(limit).offset(offset))).scalars().all()
    return Page[DatasetResponse](
        items=[DatasetResponse.model_validate(r) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


async def get_dataset(db: AsyncSession, dataset_id: UUID) -> DatasetResponse:
    ds = await db.get(Dataset, dataset_id)
    if ds is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dataset {dataset_id} not found",
        )
    return DatasetResponse.model_validate(ds)


async def preview_dataset(
    db: AsyncSession,
    dataset_id: UUID,
    limit: int,
) -> DatasetPreviewResponse:
    ds = await db.get(Dataset, dataset_id)
    if ds is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dataset {dataset_id} not found",
        )
    if not ds.storage_uri:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Dataset {dataset_id} has no rows yet (still generating?)",
        )

    bucket, key = parse_s3_uri(ds.storage_uri)
    minio = get_minio_client()
    response = minio.get_object(bucket_name=bucket, object_name=key)
    samples: list[dict] = []
    try:
        # Parse line-by-line, stopping at `limit`. Avoids buffering the whole file.
        buf = b""
        for chunk in response.stream(64 * 1024):
            buf += chunk
            while b"\n" in buf and len(samples) < limit:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    samples.append(json.loads(line.decode("utf-8")))
                except json.JSONDecodeError:
                    continue
            if len(samples) >= limit:
                break
        # Tolerate a trailing un-newlined record.
        if buf.strip() and len(samples) < limit:
            try:
                samples.append(json.loads(buf.decode("utf-8")))
            except json.JSONDecodeError:
                pass
    finally:
        response.close()
        response.release_conn()

    return DatasetPreviewResponse(
        dataset_id=ds.id,
        task_type=ds.task_type,
        samples=samples,
        total=ds.num_samples,
    )


async def download_dataset(db: AsyncSession, dataset_id: UUID) -> StreamingResponse:
    ds = await db.get(Dataset, dataset_id)
    if ds is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dataset {dataset_id} not found",
        )
    if not ds.storage_uri:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Dataset {dataset_id} has no rows yet (still generating?)",
        )

    bucket, key = parse_s3_uri(ds.storage_uri)
    filename = f"{ds.name}.jsonl"

    async def _iter() -> AsyncIterator[bytes]:
        minio = get_minio_client()
        response = minio.get_object(bucket_name=bucket, object_name=key)
        try:
            for chunk in response.stream(64 * 1024):
                yield chunk
        finally:
            response.close()
            response.release_conn()

    return StreamingResponse(
        _iter(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def delete_dataset(db: AsyncSession, dataset_id: UUID) -> None:
    ds = await db.get(Dataset, dataset_id)
    if ds is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dataset {dataset_id} not found",
        )
    # Best-effort: remove the MinIO object if any. We don't fail the DELETE
    # on storage errors because the row removal is what the user asked for.
    if ds.storage_uri:
        try:
            bucket, key = parse_s3_uri(ds.storage_uri)
            get_minio_client().remove_object(bucket_name=bucket, object_name=key)
        except Exception:  # noqa: BLE001
            log.warning("failed to remove minio object for %s", dataset_id, exc_info=True)
    await db.delete(ds)
    await db.commit()


# ---- upload-seed ----------------------------------------------------------


async def upload_seed_dataset(
    db: AsyncSession,
    *,
    project_id: UUID,
    task_type: TaskType,
    name: str | None,
    file: UploadFile,
) -> SeedUploadResponse:
    """Upload + persist a JSONL/JSON seed file for a project.

    Validates each row against the task_type's Pydantic schema; rejects rows
    that don't match (returns their indexes in `invalid_rows` so the user can
    fix and re-upload).
    """
    settings = get_settings()
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found",
        )
    if project.task_type != task_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Project task_type is {project.task_type.value} but upload "
                f"declares {task_type.value}"
            ),
        )

    # Read the whole upload (capped). Multipart is buffered to disk above this
    # call by Starlette, so memory pressure is bounded already.
    raw = await file.read(_MAX_SEED_BYTES + 1)
    if len(raw) > _MAX_SEED_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"seed file exceeds {_MAX_SEED_BYTES // (1024*1024)} MiB cap",
        )
    if not raw.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="seed file is empty",
        )

    rows, invalid_indexes = _parse_and_validate_seed(raw, task_type)
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"no valid rows after parsing (invalid_rows: {invalid_indexes})",
        )

    # Persist the row + push JSONL to MinIO under `seeds/{dataset_id}.jsonl`.
    dataset_name = name or f"seed-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    dataset = Dataset(
        project_id=project.id,
        name=dataset_name,
        task_type=task_type,
        source=DatasetSource.SEED,
        num_samples=len(rows),
    )
    db.add(dataset)
    await db.flush()

    minio = get_minio_client()
    key = f"seeds/{dataset.id}.jsonl"
    bucket = settings.minio_datasets_bucket
    size_bytes = put_jsonl(minio, bucket, key, rows)
    dataset.storage_uri = s3_uri(bucket, key)
    dataset.size_bytes = size_bytes
    await db.commit()
    await db.refresh(dataset)

    return SeedUploadResponse(
        dataset_id=dataset.id,
        task_type=task_type,
        num_samples=len(rows),
        invalid_rows=invalid_indexes,
    )


def _parse_and_validate_seed(
    raw: bytes, task_type: TaskType
) -> tuple[list[dict], list[int]]:
    """Parse JSON or JSONL bytes; validate each row; return `(valid_rows, invalid_indexes)`.

    Accepts either a JSON array of objects OR a JSONL file (one object per line).
    """
    text = raw.decode("utf-8", errors="replace").strip()
    candidates: list[dict] = []
    if text.startswith("["):
        try:
            arr = json.loads(text)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"seed file is not valid JSON: {exc}",
            ) from exc
        if not isinstance(arr, list):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="seed JSON must be a top-level array",
            )
        candidates = [r for r in arr if isinstance(r, dict)]
    else:
        for line in io.StringIO(text):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
                if isinstance(obj, dict):
                    candidates.append(obj)
            except json.JSONDecodeError:
                # Soft-tolerate: count as invalid row below.
                candidates.append({"_unparseable": True})

    valid: list[dict] = []
    invalid: list[int] = []
    for idx, row in enumerate(candidates):
        if row.get("_unparseable"):
            invalid.append(idx)
            continue
        try:
            parse_samples(task_type, [row])
            valid.append(row)
        except Exception:  # noqa: BLE001 — row-level rejection
            invalid.append(idx)
    return valid, invalid


__all__ = [
    "list_datasets",
    "get_dataset",
    "preview_dataset",
    "download_dataset",
    "delete_dataset",
    "upload_seed_dataset",
]
