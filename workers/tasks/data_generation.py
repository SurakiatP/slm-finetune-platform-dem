"""Celery task: generate a synthetic dataset and persist it.

Public progress is published to the Redis channel `job:{celery_task_id}` —
the WebSocket endpoint subscribes there. The Celery `result_backend` is
secondary (used only for terminal status from API polling, if needed).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from celery.utils.log import get_task_logger
from pydantic import TypeAdapter

from ai_engine.data_gen.generator import (
    GenerationProgress,
    SyntheticDataGenerator,
)
from ai_engine.data_gen.openrouter_client import OpenRouterClient
from api.core.config import get_settings
from api.models.dataset import Dataset
from api.schemas.progress import JobCompleted, JobFailed, SDGProgress
from api.schemas.sdg import (
    SDGRequest,
    SDGRequestDescriptionOnly,
    SDGRequestWithSeed,
)
from workers.celery_app import celery_app
from workers.progress import publish_ws_message, sync_redis_scope
from workers.storage import get_minio_client, put_jsonl, s3_uri
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
    """Run one SDG job. Publishes progress to Redis, persists JSONL to MinIO,
    and updates the `Dataset` row on completion.

    Args:
        request_payload: serialized `SDGRequest` (mode-discriminated).
        dataset_id: pre-created dataset row id (UUID string).

    Returns:
        Result dict for the Celery backend; the user-facing progress comes via
        the WebSocket channel.
    """
    job_id: str = self.request.id
    settings = get_settings()
    request = _sdg_adapter.validate_python(request_payload)
    dataset_uuid = UUID(dataset_id)

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
                ),
            )

        try:
            client = OpenRouterClient(
                api_key=settings.openrouter_api_key,
                teacher_model=settings.openrouter_teacher_model,
                http_referer=settings.openrouter_http_referer,
                app_title=settings.openrouter_app_title,
            )
            generator = SyntheticDataGenerator(client)

            log.info(
                "SDG starting: job=%s dataset=%s task=%s mode=%s target=%d",
                job_id,
                dataset_id,
                request.task_type.value,
                request.sdg_mode.value,
                request.num_samples,
            )

            result = generator.generate(request, progress_cb=emit_progress)

            # ---- Persist to MinIO --------------------------------------------------
            emit_progress(
                GenerationProgress(
                    phase="persisting",
                    samples_generated=len(result.valid_rows),
                    samples_target=request.num_samples,
                    samples_valid=len(result.valid_rows),
                    samples_rejected=result.rejected_count,
                    duplicates_removed=result.duplicate_count,
                )
            )
            minio = get_minio_client()
            bucket = settings.minio_datasets_bucket
            key = f"sdg/{dataset_id}.jsonl"
            size_bytes = put_jsonl(minio, bucket, key, result.valid_rows)
            uri = s3_uri(bucket, key)

            # ---- Update Dataset row ------------------------------------------------
            with session_scope() as session:
                dataset = session.get(Dataset, dataset_uuid)
                if dataset is None:
                    raise RuntimeError(
                        f"Dataset {dataset_id} disappeared mid-generation"
                    )
                dataset.num_samples = len(result.valid_rows)
                dataset.storage_uri = uri
                dataset.size_bytes = size_bytes
                meta = dict(dataset.generation_metadata or {})
                meta.update(
                    {
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                        "rejected_count": result.rejected_count,
                        "duplicate_count": result.duplicate_count,
                        "api_calls": result.api_calls,
                    }
                )
                dataset.generation_metadata = meta

            # ---- Publish completion ------------------------------------------------
            publish_ws_message(
                redis,
                job_id,
                JobCompleted(
                    job_id=job_id,
                    result={
                        "samples_generated": len(result.valid_rows),
                        "rejected_count": result.rejected_count,
                        "duplicate_count": result.duplicate_count,
                        "api_calls": result.api_calls,
                        "storage_uri": uri,
                        "size_bytes": size_bytes,
                    },
                    dataset_id=dataset_uuid,
                ),
            )

            log.info(
                "SDG done: job=%s dataset=%s samples=%d rejected=%d dup=%d calls=%d",
                job_id,
                dataset_id,
                len(result.valid_rows),
                result.rejected_count,
                result.duplicate_count,
                result.api_calls,
            )

            return {
                "status": "completed",
                "dataset_id": dataset_id,
                "samples_generated": len(result.valid_rows),
                "rejected_count": result.rejected_count,
                "duplicate_count": result.duplicate_count,
                "storage_uri": uri,
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
            except Exception:  # noqa: BLE001 — never let publish failure mask the original
                log.warning("failed to publish JobFailed message", exc_info=True)
            raise
