"""Celery task: run one manual fine-tune.

The flow:
  1. Load `TrainingJob` row + linked `Dataset` row from Postgres.
  2. Download the dataset's JSONL from MinIO.
  3. Open an MLflow run + open the trainer.
  4. Train under a progress callback that streams `TrainingProgress` to Redis.
  5. Upload the saved LoRA adapter directory to MinIO.
  6. Insert `ModelArtifact` and flip `TrainingJob.status = COMPLETED`.
  7. ALWAYS run `torch.cuda.empty_cache()` + `gc.collect()` in `finally` (ADR-002).

Progress is published to `job:{celery_task_id}` — the WebSocket subscribes there.
"""

from __future__ import annotations

import gc
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from celery.utils.log import get_task_logger

from ai_engine.training.callbacks import make_progress_callback
from ai_engine.training.mlflow_logger import (
    log_metrics_dict,
    log_params_flat,
    mlflow_run_scope,
)
from ai_engine.training.unsloth_trainer import TrainingResult, UnslothTrainer
from api.core.config import get_settings
from api.models.dataset import Dataset
from api.models.model_artifact import ModelArtifact
from api.models.training_job import TrainingJob
from api.schemas.enums import JobStatus
from api.core import request_context
from api.schemas.progress import JobCompleted, JobFailed
from api.services import audit_service
from api.schemas.training import ManualTrainingConfig
from workers.celery_app import celery_app
from workers.progress import publish_ws_message, sync_redis_scope
from workers.storage import (
    get_jsonl,
    get_minio_client,
    parse_s3_uri,
    put_directory,
    s3_uri,
)
from workers.sync_db import session_scope

log = get_task_logger(__name__)


@celery_app.task(bind=True, name="train.manual", max_retries=0)
def train_manual(
    self,
    *,
    training_id: str,
) -> dict[str, Any]:
    """Run one manual fine-tune.

    Args:
        training_id: UUID of the pre-inserted `TrainingJob` row.
    """
    job_id: str = self.request.id
    settings = get_settings()
    training_uuid = UUID(training_id)

    with sync_redis_scope() as redis:

        def publish(message: Any) -> None:
            publish_ws_message(redis, job_id, message)

        result: TrainingResult | None = None
        artifact_uri: str | None = None

        try:
            # ---- 1. Load TrainingJob + Dataset (sync DB) -----------------------
            ctx = _load_train_context(
                training_uuid=training_uuid,
                training_id=training_id,
                job_id=job_id,
            )
            base_model = ctx.base_model
            config = ctx.config
            task_type = ctx.task_type
            tool_definitions = ctx.tool_definitions
            dataset_uri = ctx.dataset_uri
            training_name = ctx.training_name

            # ---- 2. Pull dataset rows from MinIO -------------------------------
            log.info(
                "training: job=%s loading dataset %s",
                job_id,
                dataset_uri,
            )
            minio = get_minio_client()
            ds_bucket, ds_key = parse_s3_uri(dataset_uri)
            rows = get_jsonl(minio, ds_bucket, ds_key)
            if not rows:
                raise RuntimeError(f"Dataset {dataset_uri} is empty")

            # ---- 3. MLflow run -------------------------------------------------
            with mlflow_run_scope(
                experiment_name=f"slm/{task_type.value}",
                run_name=training_name,
                tags={
                    "training_id": training_id,
                    "celery_job_id": job_id,
                    "task_type": task_type.value,
                    "base_model": base_model,
                    "mode": "manual",
                },
            ) as mlflow_run:
                # Persist the run id back on the job row (so the API can deep-link).
                with session_scope() as session:
                    job_row = session.get(TrainingJob, training_uuid)
                    if job_row is not None:
                        job_row.mlflow_run_id = mlflow_run.run_id
                        job_row.mlflow_experiment_id = mlflow_run.experiment_id

                log_params_flat(
                    {
                        "base_model": base_model,
                        "task_type": task_type.value,
                        "num_samples": len(rows),
                        "config": config.model_dump(mode="json"),
                    }
                )

                # ---- 4. Train ---------------------------------------------------
                workdir = tempfile.mkdtemp(prefix=f"train-{training_id}-")
                try:
                    trainer = UnslothTrainer(
                        base_model=base_model,
                        config=config,
                        task_type=task_type,
                        tool_definitions=tool_definitions,
                        output_dir=workdir,
                    )
                    callback = make_progress_callback(job_id=job_id, publish=publish)
                    result = trainer.train(rows, callbacks=[callback])

                    log_metrics_dict({k: v for k, v in result.metrics.items()})

                    # ---- 5. Upload adapter to MinIO ------------------------------
                    artifact_key = f"adapters/{training_id}"
                    file_count, size_bytes = put_directory(
                        minio,
                        settings.minio_models_bucket,
                        artifact_key,
                        result.adapter_dir,
                    )
                    artifact_uri = s3_uri(settings.minio_models_bucket, artifact_key)
                    log.info(
                        "training: job=%s uploaded %d adapter files (%.1f MB) to %s",
                        job_id,
                        file_count,
                        size_bytes / (1024 * 1024),
                        artifact_uri,
                    )
                finally:
                    # Always remove the local tempdir; adapter is now on MinIO.
                    shutil.rmtree(workdir, ignore_errors=True)

            # ---- 6. Persist ModelArtifact + flip COMPLETED ---------------------
            artifact_id = _persist_artifact(
                training_uuid=training_uuid,
                training_id=training_id,
                training_name=training_name,
                base_model=base_model,
                artifact_uri=artifact_uri,
                size_bytes=size_bytes,
            )

            # ---- 7. Publish completion -----------------------------------------
            publish(
                JobCompleted(
                    job_id=job_id,
                    result={
                        "training_id": training_id,
                        "model_artifact_id": str(artifact_id),
                        "lora_adapter_uri": artifact_uri,
                        "final_train_loss": result.final_train_loss,
                        "final_eval_loss": result.final_eval_loss,
                        "steps_completed": result.steps_completed,
                        "train_runtime_seconds": result.train_runtime_seconds,
                        "metrics": result.metrics,
                    },
                    mlflow_run_id=_load_run_id(training_uuid),
                    model_artifact_id=artifact_id,
                )
            )

            log.info(
                "training: job=%s done (steps=%d, train_loss=%s, eval_loss=%s)",
                job_id,
                result.steps_completed,
                result.final_train_loss,
                result.final_eval_loss,
            )

            return {
                "status": "completed",
                "training_id": training_id,
                "model_artifact_id": str(artifact_id),
                "lora_adapter_uri": artifact_uri,
                "final_train_loss": result.final_train_loss,
                "final_eval_loss": result.final_eval_loss,
            }

        except BaseException as exc:
            # BaseException, not Exception: `POST /trainings/{id}/cancel` revokes
            # this task with `celery_app.control.revoke(terminate=True,
            # signal="SIGTERM")`. Billiard's worker-child signal handler turns
            # that SIGTERM into `sys.exit(...)` — a `SystemExit` raised inside
            # this task body, which `except Exception` does NOT catch, so
            # cancelling a training skipped this whole block and no `JobFailed`
            # frame was ever published. A WebSocket-only client (which is what
            # `smart-model-tune`'s `useTrainingWebSocket` is) then sat on the
            # last `training_progress` frame forever. Verified on real hardware
            # before this was widened: DB row `cancelled`, but `job:{id}:last`
            # still held a mid-run progress frame.
            # `data_generation.py` / `model_export.py` / `evaluation.py` carry
            # the same treatment; this path and `hpo_training.py` were missed
            # when 523aded landed.
            log.exception("training task failed (job=%s)", job_id)
            # Mark job FAILED in DB before re-raising; let publish be best-effort.
            try:
                with session_scope() as session:
                    job_row = session.get(TrainingJob, training_uuid)
                    if job_row is not None:
                        # The cancel endpoint sets status=CANCELLED *before*
                        # revoking. Don't clobber it back to FAILED — CANCELLED
                        # is the accurate terminal state for that run. The error
                        # message is still recorded either way.
                        if job_row.status != JobStatus.CANCELLED:
                            job_row.status = JobStatus.FAILED
                        job_row.ended_at = datetime.now(timezone.utc)
                        job_row.error_message = (str(exc) or repr(exc))[:4000]
                        audit_service.record(
                            session,
                            action=(
                                "training.cancelled"
                                if job_row.status == JobStatus.CANCELLED
                                else "training.failed"
                            ),
                            resource_type="training",
                            resource_id=str(job_row.id),
                            project_id=job_row.project_id,
                            outcome="failure",
                            request_id=request_context.current_request_id(),
                            metadata={"job_id": job_id, "error_type": type(exc).__name__},
                        )

            except Exception:  # noqa: BLE001 — never mask the original failure
                log.warning("could not persist FAILED status for %s", training_id, exc_info=True)
            try:
                publish(
                    JobFailed(
                        job_id=job_id,
                        error=str(exc) or repr(exc),
                        error_type=type(exc).__name__,
                    )
                )
            except Exception:  # noqa: BLE001
                log.warning("failed to publish JobFailed message", exc_info=True)
            raise

        finally:
            # ---- 8. GPU cleanup (ADR-002 STRICT) ------------------------------
            _release_gpu_memory()


# ---- helpers ---------------------------------------------------------------


@dataclass(frozen=True)
class _TrainContext:
    """Snapshot of TrainingJob + Dataset fields needed for one fine-tune run.

    Built by `_load_train_context` inside a brief DB session so the caller can
    drop the session before kicking off heavy GPU work.
    """

    base_model: str
    config: ManualTrainingConfig
    task_type: Any                       # api.schemas.enums.TaskType (avoid import cycle)
    tool_definitions: list | None
    dataset_uri: str
    training_name: str


def _load_train_context(
    *,
    training_uuid: UUID,
    training_id: str,
    job_id: str,
) -> _TrainContext:
    """Load TrainingJob + Dataset, validate, flip RUNNING, and snapshot fields.

    Pulled out of `train_manual` so the orchestrator reads top-down. Keeps the
    same DB session boundary: open → validate → snapshot → set status →
    commit on context exit, before any heavy work begins.
    """
    with session_scope() as session:
        job = session.get(TrainingJob, training_uuid)
        if job is None:
            raise RuntimeError(f"TrainingJob {training_id} not found")
        if job.mode.value != "manual":
            raise RuntimeError(
                f"train.manual called on job with mode={job.mode.value}"
            )

        dataset = session.get(Dataset, job.dataset_id)
        if dataset is None:
            raise RuntimeError(
                f"Dataset {job.dataset_id} not found for training {training_id}"
            )
        if not dataset.storage_uri:
            raise RuntimeError(
                f"Dataset {dataset.id} has no storage_uri — generation may have failed"
            )

        ctx = _TrainContext(
            base_model=job.base_model,
            config=ManualTrainingConfig.model_validate(job.config_json),
            task_type=dataset.task_type,
            tool_definitions=_extract_tool_definitions(dataset.generation_metadata),
            dataset_uri=dataset.storage_uri,
            training_name=job.training_name or f"manual-{job_id[:8]}",
        )

        # Flip RUNNING + record started_at (committed on session-scope exit).
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)

    return ctx


def _persist_artifact(
    *,
    training_uuid: UUID,
    training_id: str,
    training_name: str,
    base_model: str,
    artifact_uri: str,
    size_bytes: int,
) -> UUID:
    """Insert ModelArtifact + flip TrainingJob.COMPLETED + return artifact_id.

    Pulled out of `train_manual` so the post-training DB step is one call.
    Same single-session boundary as before — both writes commit together.
    """
    with session_scope() as session:
        job_row = session.get(TrainingJob, training_uuid)
        if job_row is None:
            raise RuntimeError(
                f"TrainingJob {training_id} disappeared mid-run"
            )
        artifact = ModelArtifact(
            training_job_id=job_row.id,
            name=training_name,
            base_model=base_model,
            mlflow_run_id=job_row.mlflow_run_id,
            lora_adapter_uri=artifact_uri,
            size_mb=round(size_bytes / (1024 * 1024), 2),
        )
        session.add(artifact)
        session.flush()
        artifact_id = artifact.id

        job_row.status = JobStatus.COMPLETED
        job_row.ended_at = datetime.now(timezone.utc)
        audit_service.record(
            session,
            action="training.completed",
            resource_type="training",
            resource_id=str(job_row.id),
            project_id=job_row.project_id,
            request_id=request_context.current_request_id(),
            metadata={
                "job_id": job_row.celery_task_id,
                "model_artifact_id": str(artifact_id),
            },
        )
    return artifact_id


def _extract_tool_definitions(metadata: dict[str, Any] | None) -> list | None:
    """Pull `tool_definitions` from a Dataset's generation_metadata, if present.

    Datasets generated in tool_calling/description_only mode stash the tool
    schema there; tool_calling/with_seed datasets don't (the formatter then
    falls back to the generic ChatML system prompt).
    """
    if not metadata:
        return None
    raw = metadata.get("tool_definitions")
    if not raw:
        return None
    # Lazy import to avoid pulling pydantic into hot path unnecessarily.
    from api.schemas.data_formats import ToolDefinition

    return [ToolDefinition.model_validate(d) for d in raw]


def _load_run_id(training_uuid: UUID) -> str | None:
    """Re-read mlflow_run_id from DB (set inside the run scope)."""
    with session_scope() as session:
        job = session.get(TrainingJob, training_uuid)
        return job.mlflow_run_id if job else None


def _release_gpu_memory() -> None:
    """Best-effort CUDA cleanup. Safe to call when torch isn't installed."""
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except Exception:  # noqa: BLE001
        log.debug("torch cleanup skipped", exc_info=True)
    gc.collect()


__all__ = ["train_manual"]
