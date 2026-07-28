"""Application service for `/api/v1/datasets` (read + delete + upload-seed).

The SDG generation endpoint lives in `sdg_service.py` (Phase 4); this module
covers the rest of the dataset surface.

Phase 9 changes:
  • upload-seed accepts `.pdf` for QA — runs `pdf_loader.probe`, persists
    the raw bytes to `seed-pdfs/{dataset_id}.pdf`, sets `pdf_uri` in
    metadata.
  • upload-seed runs Format Detection on `.json/.jsonl` whenever the
    rows aren't already canonical. The mapping + audit report are
    persisted in `Dataset.generation_metadata['format_detection']`.
  • delete-dataset cleans up both the JSONL and the PDF (if any).
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from datetime import datetime, timezone
from io import BytesIO
from typing import AsyncIterator
from uuid import UUID

from fastapi import HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_engine.data_gen import models as llm_models
from ai_engine.data_gen.format_detector import (
    FormatDetectionResult,
    detect_and_rename,
    passthrough_with_required_check,
)
from ai_engine.data_gen.openrouter_client import OpenRouterClient
from ai_engine.data_gen.semantic_guard import SemanticGuardError, assert_semantic_fit
from ai_engine.data_gen.pdf_loader import (
    PdfCorruptError,
    PdfTooLargeError,
    PdfTooManyPagesError,
    probe as pdf_probe,
)
from ai_engine.data_gen.constants import MAX_SEED_PDF_BYTES
from api.core.config import get_settings
from api.models.dataset import Dataset
from api.models.evaluation_run import EvaluationRun
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.data_formats import (
    canonical_field_names,
    parse_samples,
    required_field_names,
)
from api.schemas.datasets import DatasetPreviewResponse, DatasetResponse
from api.schemas.enums import DatasetSource, JobStatus, TaskType
from api.schemas.responses import Page
from api.schemas.sdg import SeedUploadResponse
from api.schemas.upload import FormatDetectionReport
from workers.storage import (
    get_minio_client,
    parse_s3_uri,
    put_jsonl,
    s3_uri,
)

log = logging.getLogger(__name__)

# Cap on uploaded JSON/JSONL seed file size (MiB).
_MAX_SEED_BYTES = 10 * 1024 * 1024
# Cap on uploaded PDF seed file size (Phase 9, MAX_SEED_PDF_BYTES = 25 MiB).
_MAX_SEED_PDF_BYTES = MAX_SEED_PDF_BYTES


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

    # Both `training_jobs.dataset_id` and `evaluation_runs.dataset_id` are
    # NOT NULL with `ondelete=RESTRICT` — we must refuse the delete here
    # rather than let the FK constraint surface as a 500.
    n_trainings = (
        await db.execute(
            select(func.count())
            .select_from(TrainingJob)
            .where(TrainingJob.dataset_id == dataset_id)
        )
    ).scalar_one()
    n_evals = (
        await db.execute(
            select(func.count())
            .select_from(EvaluationRun)
            .where(EvaluationRun.dataset_id == dataset_id)
        )
    ).scalar_one()
    if n_trainings or n_evals:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Dataset {dataset_id} is referenced by "
                f"{n_trainings} training_job(s) and {n_evals} evaluation_run(s); "
                "delete those first or DELETE the parent project to cascade."
            ),
        )

    # Best-effort: remove MinIO objects (JSONL + PDF if any). Phase 9
    # adds the PDF cleanup — SEED + PDF datasets have two objects.
    minio = get_minio_client()
    if ds.storage_uri:
        try:
            bucket, key = parse_s3_uri(ds.storage_uri)
            minio.remove_object(bucket_name=bucket, object_name=key)
        except Exception:  # noqa: BLE001
            log.warning("failed to remove minio object for %s", dataset_id, exc_info=True)
    pdf_uri = (ds.generation_metadata or {}).get("pdf_uri")
    if pdf_uri:
        try:
            bucket, key = parse_s3_uri(pdf_uri)
            minio.remove_object(bucket_name=bucket, object_name=key)
        except Exception:  # noqa: BLE001
            log.warning(
                "failed to remove pdf object for %s", dataset_id, exc_info=True
            )

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
    """Upload + persist a JSONL/JSON or PDF (QA-only) seed file.

    Phase 9 flow:
      .pdf  → QA only; probe page/byte caps; persist raw bytes;
              set Dataset.generation_metadata['pdf_uri'].
      else  → parse JSON or JSONL; if rows aren't canonical, run Format
              Detection (LLM); persist canonicalised JSONL; record the
              FormatDetectionReport in metadata.
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

    is_pdf = _looks_like_pdf(file)
    if is_pdf:
        return await _upload_pdf_seed(
            db,
            settings=settings,
            project=project,
            task_type=task_type,
            name=name,
            file=file,
        )
    return await _upload_jsonl_seed(
        db,
        settings=settings,
        project=project,
        task_type=task_type,
        name=name,
        file=file,
    )


# ---- upload-seed: JSONL/JSON path -----------------------------------------


async def _upload_jsonl_seed(
    db: AsyncSession,
    *,
    settings,
    project: Project,
    task_type: TaskType,
    name: str | None,
    file: UploadFile,
) -> SeedUploadResponse:
    # Stage 1: read + parse the upload bytes into row candidates.
    candidates = await _read_and_parse_jsonl_upload(file)

    # Stage 2: Format Detection — Gemini key-rename when rows aren't
    # already canonical. asyncio.to_thread keeps the event loop free.
    canonical_keys = canonical_field_names(task_type)
    required_keys = required_field_names(task_type)
    fd_result = await _run_format_detection(
        rows=candidates,
        canonical_keys=canonical_keys,
        required_keys=required_keys,
        task_type=task_type,
        api_key=settings.openrouter_api_key,
        http_referer=settings.openrouter_http_referer,
        app_title=settings.openrouter_app_title,
    )

    # Stage 3: per-row Pydantic validation + semantic guard.
    valid, invalid = _validate_canonical_rows(fd_result.canonical_rows, task_type)
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"no valid rows after Format Detection + schema validation "
                f"(format_detection={fd_result.notes or 'ran'}, "
                f"invalid_indexes={invalid})"
            ),
        )
    # Semantic guard: rows passed structural validation but may still be the
    # wrong *kind* of content (e.g. a QA file Format-Detected into a
    # classification project — Session 22 Finding #1). Currently only
    # classification has a semantic guard; tool_calling is enforced by the
    # ToolCallingSample Pydantic validator, qa has no closed set to enforce.
    try:
        assert_semantic_fit(valid, task_type)
    except SemanticGuardError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    # Stage 4: persist Dataset row + JSONL to MinIO.
    fd_report = _to_report(
        fd_result,
        rows_total=len(candidates),
        rows_canonicalised=len(fd_result.canonical_rows),
        model_used=llm_models.FORMAT_DETECTION if fd_result.ran else None,
    )
    dataset = await _persist_jsonl_dataset(
        db,
        settings=settings,
        project=project,
        task_type=task_type,
        name=name,
        valid_rows=valid,
        fd_report=fd_report,
    )

    return SeedUploadResponse(
        dataset_id=dataset.id,
        task_type=task_type,
        num_samples=len(valid),
        invalid_rows=invalid,
        format_detection=fd_report,
    )


async def _read_and_parse_jsonl_upload(file: UploadFile) -> list[dict]:
    """Read the upload bytes, enforce the size + non-empty caps, return rows.

    Raises HTTPException 413/400 on cap breach / empty input / unparseable
    content. Centralised here so :func:`_upload_jsonl_seed` doesn't open
    with 25 lines of input plumbing.
    """
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
    candidates = _parse_seed_bytes(raw)
    if not candidates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="no parseable rows in seed file",
        )
    return candidates


def _validate_canonical_rows(
    rows: list[dict], task_type: TaskType
) -> tuple[list[dict], list[int]]:
    """Pydantic-validate each row; return (valid_rows, invalid_indexes).

    Pulled out of :func:`_upload_jsonl_seed` so the validation seam is
    one named thing — Stage 3 of the upload pipeline.
    """
    valid: list[dict] = []
    invalid: list[int] = []
    for idx, row in enumerate(rows):
        try:
            parse_samples(task_type, [row])
            valid.append(row)
        except Exception:  # noqa: BLE001 — row-level rejection
            invalid.append(idx)
    return valid, invalid


async def _persist_jsonl_dataset(
    db: AsyncSession,
    *,
    settings,
    project: Project,
    task_type: TaskType,
    name: str | None,
    valid_rows: list[dict],
    fd_report: FormatDetectionReport,
) -> Dataset:
    """Create the Dataset row + write the canonicalised JSONL to MinIO.

    Returns the freshly-refreshed Dataset (so the caller can read
    ``dataset.id`` for the response). Stage 4 of the upload pipeline.
    """
    dataset_name = name or f"seed-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    dataset = Dataset(
        project_id=project.id,
        name=dataset_name,
        task_type=task_type,
        source=DatasetSource.SEED,
        status=JobStatus.COMPLETED,
        num_samples=len(valid_rows),
        generation_metadata={"format_detection": fd_report.model_dump()},
    )
    db.add(dataset)
    await db.flush()

    minio = get_minio_client()
    key = f"seeds/{dataset.id}.jsonl"
    bucket = settings.minio_datasets_bucket
    size_bytes = put_jsonl(minio, bucket, key, valid_rows)
    dataset.storage_uri = s3_uri(bucket, key)
    dataset.size_bytes = size_bytes
    await db.commit()
    await db.refresh(dataset)
    return dataset


def _parse_seed_bytes(raw: bytes) -> list[dict]:
    """Parse JSON-array OR JSONL bytes into a list of row dicts.

    Tolerates blank lines and unparseable lines (the latter come back as
    `{"_unparseable": True}` and are dropped before Format Detection — we
    don't want to feed garbage into the LLM mapper).
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
                # Skip unparseable lines silently — Format Detection can't
                # do anything useful with them.
                continue
    return candidates


async def _run_format_detection(
    *,
    rows: list[dict],
    canonical_keys: set[str],
    required_keys: set[str],
    task_type: TaskType,
    api_key: str,
    http_referer: str,
    app_title: str,
) -> FormatDetectionResult:
    """Run Format Detection in a thread so we don't block the event loop.

    The sync OpenRouterClient is the right choice here — Format Detection is
    a single LLM call per upload, not a batch. asyncio.to_thread keeps the
    FastAPI event loop free for other requests.
    """

    def _go() -> FormatDetectionResult:
        if not api_key:
            log.warning(
                "OPENROUTER_API_KEY is empty — skipping Format Detection; "
                "treating rows as raw and dropping any with missing required keys"
            )
            return passthrough_with_required_check(
                rows, required_keys, notes="OPENROUTER_API_KEY not set"
            )
        client = OpenRouterClient(
            api_key=api_key,
            teacher_model=llm_models.FORMAT_DETECTION,
            http_referer=http_referer,
            app_title=app_title,
        )
        return detect_and_rename(
            rows=rows,
            canonical_keys=canonical_keys,
            required_keys=required_keys,
            task_type_label=task_type.value,
            client=client,
        )

    return await asyncio.to_thread(_go)


# ---- upload-seed: PDF path ------------------------------------------------


async def _upload_pdf_seed(
    db: AsyncSession,
    *,
    settings,
    project: Project,
    task_type: TaskType,
    name: str | None,
    file: UploadFile,
) -> SeedUploadResponse:
    if task_type != TaskType.QA:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"PDF uploads are supported only for task_type=qa "
                f"(got {task_type.value})"
            ),
        )

    raw, probe_result = await _read_and_probe_pdf_upload(file)

    fd_report = FormatDetectionReport(
        ran=False,
        model_used=None,
        field_mapping={},
        rows_total=0,
        rows_canonicalised=0,
        rows_dropped=0,
        notes="PDF upload — Format Detection not applicable",
    )

    dataset, pdf_uri = await _persist_pdf_dataset(
        db,
        settings=settings,
        project=project,
        task_type=task_type,
        name=name,
        raw=raw,
        num_pages=probe_result.num_pages,
        fd_report=fd_report,
    )

    return SeedUploadResponse(
        dataset_id=dataset.id,
        task_type=task_type,
        num_samples=0,
        invalid_rows=[],
        format_detection=fd_report,
        pdf_uri=pdf_uri,
    )


async def _read_and_probe_pdf_upload(file: UploadFile):
    """Read the PDF upload, enforce size cap, run pdf_loader.probe.

    Returns the raw bytes + the ``PdfProbe`` so the caller can persist
    both without re-reading. Raises HTTPException with the right status
    code for each failure mode.
    """
    raw = await file.read(_MAX_SEED_PDF_BYTES + 1)
    if len(raw) > _MAX_SEED_PDF_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"PDF exceeds {_MAX_SEED_PDF_BYTES // (1024*1024)} MiB cap",
        )
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="empty PDF upload",
        )
    try:
        probe_result = pdf_probe(raw)
    except PdfTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from exc
    except PdfTooManyPagesError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from exc
    except PdfCorruptError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return raw, probe_result


async def _persist_pdf_dataset(
    db: AsyncSession,
    *,
    settings,
    project: Project,
    task_type: TaskType,
    name: str | None,
    raw: bytes,
    num_pages: int,
    fd_report: FormatDetectionReport,
) -> tuple[Dataset, str]:
    """Create the Dataset row + write the PDF bytes to MinIO.

    Returns (refreshed Dataset, pdf_uri). Mirrors :func:`_persist_jsonl_dataset`
    so both upload paths share the same stage shape.
    """
    dataset_name = (
        name
        or f"seed-pdf-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    )
    bucket = settings.minio_datasets_bucket
    dataset = Dataset(
        project_id=project.id,
        name=dataset_name,
        task_type=task_type,
        source=DatasetSource.SEED,
        status=JobStatus.COMPLETED,
        num_samples=0,
        generation_metadata={
            "format_detection": fd_report.model_dump(),
            "pdf_pages": num_pages,
        },
    )
    db.add(dataset)
    await db.flush()

    pdf_key = f"seed-pdfs/{dataset.id}.pdf"
    minio = get_minio_client()
    minio.put_object(
        bucket_name=bucket,
        object_name=pdf_key,
        data=BytesIO(raw),
        length=len(raw),
        content_type="application/pdf",
    )
    pdf_uri = s3_uri(bucket, pdf_key)
    dataset.storage_uri = None  # No JSONL; pdf_uri is the source of truth.
    dataset.size_bytes = len(raw)
    meta = dict(dataset.generation_metadata or {})
    meta["pdf_uri"] = pdf_uri
    dataset.generation_metadata = meta
    await db.commit()
    await db.refresh(dataset)
    return dataset, pdf_uri


# ---- helpers --------------------------------------------------------------


def _looks_like_pdf(file: UploadFile) -> bool:
    """Heuristic content-type / extension dispatch for the upload-seed router."""
    if file.content_type == "application/pdf":
        return True
    fname = (file.filename or "").lower()
    return fname.endswith(".pdf")


def _to_report(
    result: FormatDetectionResult,
    *,
    rows_total: int,
    rows_canonicalised: int,
    model_used: str | None,
) -> FormatDetectionReport:
    return FormatDetectionReport(
        ran=result.ran,
        model_used=model_used if result.ran else None,
        field_mapping=result.field_mapping,
        rows_total=rows_total,
        rows_canonicalised=rows_canonicalised,
        rows_dropped=result.rows_dropped,
        notes=result.notes,
    )


__all__ = [
    "list_datasets",
    "get_dataset",
    "preview_dataset",
    "download_dataset",
    "delete_dataset",
    "upload_seed_dataset",
]
