"""Celery task: generate a synthetic dataset and persist it (Phase 9 flow).

The Celery task body itself stays sync — Celery's prefork pool handles
that natively. Inside the task, `asyncio.run(...)` is the boundary for
the new `SyntheticDataGenerator.generate(...)` async loop.

Public progress is published to the Redis channel `job:{celery_task_id}`.
The WebSocket endpoint subscribes there.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from celery.utils.log import get_task_logger
from pydantic import TypeAdapter

from ai_engine.data_gen.generator import (
    GenerationProgress,
    SyntheticDataGenerator,
)
from ai_engine.data_gen.openrouter_client import (
    AsyncOpenRouterClient,
    OpenRouterClient,
)
from api.core.config import get_settings
from api.models.dataset import Dataset
from api.schemas.enums import DatasetSource
from api.schemas.progress import JobCompleted, JobFailed, SDGProgress
from api.schemas.sdg import (
    SDGRequest,
    SDGRequestDescriptionOnly,
    SDGRequestWithSeed,
)
from workers.celery_app import celery_app
from workers.progress import publish_ws_message, sync_redis_scope
from workers.storage import get_jsonl, get_minio_client, parse_s3_uri, put_jsonl, s3_uri
from workers.sync_db import session_scope

log = get_task_logger(__name__)
_sdg_adapter: TypeAdapter[SDGRequestWithSeed | SDGRequestDescriptionOnly] = TypeAdapter(SDGRequest)


@celery_app.task(bind=True, name="sdg.generate", max_retries=0)
def generate_synthetic_data(
    self,
    *,
    request_payload: dict[str, Any],
    dataset_id: str,
) -> dict[str, Any]:
    """Run one SDG job (Phase 9). Async generator + quota + sentinel + judge.

    Args:
        request_payload: serialized `SDGRequest` (mode-discriminated).
        dataset_id: pre-created (parent) dataset row id (UUID string).
    """
    job_id: str = self.request.id
    settings = get_settings()
    request = _sdg_adapter.validate_python(request_payload)
    parent_uuid = UUID(dataset_id)
    holdout_size = request.holdout_size
    effective_target = request.num_samples + holdout_size

    with sync_redis_scope() as redis:

        def emit_progress(p: GenerationProgress) -> None:
            publish_ws_message(
                redis,
                job_id,
                SDGProgress(
                    job_id=job_id,
                    phase=p.phase,
                    samples_generated=p.samples_generated,
                    samples_target=p.samples_target,
                    samples_valid=p.samples_valid,
                    samples_rejected=p.samples_rejected,
                    duplicates_removed=p.duplicates_removed,
                    current_loop=p.current_loop,
                    judge_rejected=p.judge_rejected,
                    judge_parse_failures=p.judge_parse_failures,
                    dedup_rejected=p.dedup_rejected,
                ),
            )

        try:
            log.info(
                "SDG starting: job=%s dataset=%s task=%s mode=%s "
                "train_target=%d holdout=%d effective=%d",
                job_id,
                dataset_id,
                request.task_type.value,
                request.sdg_mode.value,
                request.num_samples,
                holdout_size,
                effective_target,
            )

            seed_rows, pdf_bytes = _load_seed_payload(request, settings)

            result = asyncio.run(
                _run_generator(
                    request=request,
                    effective_target=effective_target,
                    seed_rows=seed_rows,
                    pdf_bytes=pdf_bytes,
                    settings=settings,
                    progress_cb=emit_progress,
                )
            )

            import random as _random

            from ai_engine.data_gen.holdout_split import split_rows

            rng = _random.Random(_split_seed(request, dataset_id))
            train_rows, holdout_rows = split_rows(
                result.valid_rows,
                request.task_type,
                holdout_size,
                rng=rng,
            )

            emit_progress(
                GenerationProgress(
                    phase="persisting",
                    samples_generated=len(result.valid_rows),
                    samples_target=effective_target,
                    samples_valid=len(result.valid_rows),
                    samples_rejected=result.rejected_count,
                    duplicates_removed=result.duplicate_count,
                )
            )
            minio = get_minio_client()
            bucket = settings.minio_datasets_bucket
            train_key = f"sdg/{dataset_id}.jsonl"
            train_size = put_jsonl(minio, bucket, train_key, train_rows)
            train_uri = s3_uri(bucket, train_key)

            holdout_uuid: UUID | None = None
            holdout_uri: str | None = None
            holdout_size_bytes: int | None = None
            if holdout_rows:
                from uuid import uuid4

                holdout_uuid = uuid4()
                holdout_key = f"sdg/{holdout_uuid}.jsonl"
                holdout_size_bytes = put_jsonl(
                    minio, bucket, holdout_key, holdout_rows
                )
                holdout_uri = s3_uri(bucket, holdout_key)

            with session_scope() as session:
                parent = session.get(Dataset, parent_uuid)
                if parent is None:
                    raise RuntimeError(
                        f"Dataset {dataset_id} disappeared mid-generation"
                    )
                parent.num_samples = len(train_rows)
                parent.storage_uri = train_uri
                parent.size_bytes = train_size
                parent_meta = dict(parent.generation_metadata or {})
                parent_meta.update(
                    {
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                        "rejected_count": result.rejected_count,
                        "duplicate_count": result.duplicate_count,
                        "judge_rejected_count": result.judge_rejected_count,
                        "judge_parse_failures": result.judge_parse_failures,
                        "api_calls": result.api_calls,
                        "holdout_size_requested": holdout_size,
                        "holdout_size_actual": len(holdout_rows),
                        "role": "train",
                        "holdout_dataset_id": (
                            str(holdout_uuid) if holdout_uuid else None
                        ),
                    }
                )
                parent.generation_metadata = parent_meta

                if holdout_uuid is not None:
                    child = Dataset(
                        id=holdout_uuid,
                        project_id=parent.project_id,
                        name=f"{parent.name}-holdout",
                        task_type=parent.task_type,
                        source=DatasetSource.SDG,
                        num_samples=len(holdout_rows),
                        storage_uri=holdout_uri,
                        size_bytes=holdout_size_bytes,
                        parent_dataset_id=parent.id,
                        generation_metadata={
                            "role": "holdout",
                            "parent_dataset_id": str(parent.id),
                            "sdg_mode": request.sdg_mode.value,
                            "task_description": request.task_description,
                            "completed_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    session.add(child)

            publish_ws_message(
                redis,
                job_id,
                JobCompleted(
                    job_id=job_id,
                    result={
                        "samples_generated": len(train_rows),
                        "holdout_samples": len(holdout_rows),
                        "rejected_count": result.rejected_count,
                        "duplicate_count": result.duplicate_count,
                        "judge_rejected_count": result.judge_rejected_count,
                        "judge_parse_failures": result.judge_parse_failures,
                        "api_calls": result.api_calls,
                        "storage_uri": train_uri,
                        "holdout_storage_uri": holdout_uri,
                        "holdout_dataset_id": (
                            str(holdout_uuid) if holdout_uuid else None
                        ),
                        "size_bytes": train_size,
                    },
                    dataset_id=parent_uuid,
                ),
            )

            log.info(
                "SDG done: job=%s parent=%s holdout=%s train=%d holdout=%d "
                "rejected=%d dup=%d calls=%d",
                job_id,
                dataset_id,
                holdout_uuid,
                len(train_rows),
                len(holdout_rows),
                result.rejected_count,
                result.duplicate_count,
                result.api_calls,
            )

            return {
                "status": "completed",
                "dataset_id": dataset_id,
                "holdout_dataset_id": (
                    str(holdout_uuid) if holdout_uuid else None
                ),
                "samples_generated": len(train_rows),
                "holdout_samples": len(holdout_rows),
                "rejected_count": result.rejected_count,
                "duplicate_count": result.duplicate_count,
                "judge_rejected_count": result.judge_rejected_count,
                "storage_uri": train_uri,
                "holdout_storage_uri": holdout_uri,
            }

        except Exception as exc:
            log.exception("SDG task failed (job=%s)", job_id)
            try:
                publish_ws_message(
                    redis,
                    job_id,
                    JobFailed(
                        job_id=job_id,
                        error=str(exc) or repr(exc),
                        error_type=type(exc).__name__,
                    ),
                )
            except Exception:  # noqa: BLE001
                log.warning("failed to publish JobFailed message", exc_info=True)
            raise


# ---- helpers --------------------------------------------------------------


async def _run_generator(
    *,
    request: SDGRequestWithSeed | SDGRequestDescriptionOnly,
    effective_target: int,
    seed_rows: list[dict[str, Any]],
    pdf_bytes: bytes | None,
    settings,
    progress_cb,
):
    """Set up async + sync clients, run the generator with an explicit target.

    `effective_target = num_samples + holdout_size` when holdout is requested;
    otherwise equals num_samples.
    """
    effective_request = request.model_copy(update={"num_samples": effective_target})
    sync_client = OpenRouterClient(
        api_key=settings.openrouter_api_key,
        teacher_model="placeholder/unused",
        http_referer=settings.openrouter_http_referer,
        app_title=settings.openrouter_app_title,
    )
    async with AsyncOpenRouterClient(
        api_key=settings.openrouter_api_key,
        http_referer=settings.openrouter_http_referer,
        app_title=settings.openrouter_app_title,
    ) as async_client:
        gen = SyntheticDataGenerator(async_client, sync_client)
        return await gen.generate(
            effective_request,
            seed_rows=seed_rows,
            pdf_bytes=pdf_bytes,
            progress_cb=progress_cb,
        )


def _load_seed_payload(
    request: SDGRequestWithSeed | SDGRequestDescriptionOnly,
    settings,
) -> tuple[list[dict[str, Any]], bytes | None]:
    """For with_seed: fetch canonical JSONL from MinIO (and PDF bytes if any).

    For description_only: returns ([], None).
    """
    if isinstance(request, SDGRequestDescriptionOnly):
        return [], None

    seed_dataset_id = request.seed_dataset_id
    minio = get_minio_client()
    seed_rows: list[dict[str, Any]] = []
    pdf_bytes: bytes | None = None

    with session_scope() as session:
        seed_ds = session.get(Dataset, seed_dataset_id)
        if seed_ds is None:
            raise RuntimeError(
                f"seed dataset {seed_dataset_id} not found in DB"
            )
        if seed_ds.source != DatasetSource.SEED:
            raise RuntimeError(
                f"seed_dataset_id {seed_dataset_id} is source={seed_ds.source.value}, "
                f"expected seed"
            )
        meta = seed_ds.generation_metadata or {}
        pdf_uri = meta.get("pdf_uri")
        # JSONL rows (when present)
        if seed_ds.storage_uri:
            bucket, key = parse_s3_uri(seed_ds.storage_uri)
            seed_rows = get_jsonl(minio, bucket, key)
        # PDF bytes (QA + PDF only)
        if pdf_uri:
            bucket, key = parse_s3_uri(pdf_uri)
            response = minio.get_object(bucket_name=bucket, object_name=key)
            try:
                pdf_bytes = response.read()
            finally:
                response.close()
                response.release_conn()

    return seed_rows, pdf_bytes


def _split_seed(
    request: SDGRequestWithSeed | SDGRequestDescriptionOnly,
    dataset_id: str,
) -> int:
    """Deterministic split seed derived from request + dataset id.

    Keeping this deterministic means re-running the same SDG request against
    the same dataset_id produces the same train/holdout assignment - useful
    if a downstream step crashed and the operator needs to retry.
    """
    raw = f"{dataset_id}|{request.task_type.value}|{request.holdout_size}"
    return abs(hash(raw)) % (2**31 - 1)


__all__ = ["generate_synthetic_data"]
