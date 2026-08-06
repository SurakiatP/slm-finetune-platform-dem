"""Celery task: run an Optuna HPO study, then retrain on the best params.

Flow:
  1. Load `TrainingJob` (mode=hpo) + linked `Dataset`.
  2. Pull dataset rows from MinIO.
  3. Open the parent MLflow run; log the search space + n_trials as params.
  4. Build `HPOObjective` and run `study.optimize(...)`. Trial-progress events
     turn into `HPOProgress` messages on `job:{id}`.
  5. After the study finishes, retrain ONE final model on `study.best_params`.
     This run uses the same parent MLflow run as a `nested=True` child named
     `best`. The adapter from this final run is what we persist.
  6. Upload the final adapter, insert `ModelArtifact`, persist
     `best_metric_value` + `best_params_json`, flip `COMPLETED`.
  7. Always run GPU cleanup in `finally` (ADR-002).
"""

from __future__ import annotations

import gc
import shutil
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from celery.utils.log import get_task_logger

from ai_engine.hpo.optuna_objective import (
    HPOObjective,
    TrialOutcome,
    make_optuna_workdir,
)
from ai_engine.hpo.search_spaces import best_params_to_config
from ai_engine.training.callbacks import make_progress_callback
from ai_engine.training.mlflow_logger import (
    log_metrics_dict,
    log_params_flat,
    mlflow_run_scope,
)
from ai_engine.training.unsloth_trainer import UnslothTrainer
from api.core.config import get_settings
from api.models.dataset import Dataset
from api.models.model_artifact import ModelArtifact
from api.models.training_job import TrainingJob
from api.schemas.enums import JobStatus
from api.core import request_context
from api.schemas.progress import HPOProgress, JobCompleted, JobFailed
from api.services import audit_service
from api.schemas.training import HPOConfig
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


@celery_app.task(bind=True, name="train.hpo", max_retries=0)
def train_hpo(
    self,
    *,
    training_id: str,
) -> dict[str, Any]:
    """Run one HPO study + a final retrain on the best params.

    Args:
        training_id: UUID of the pre-inserted `TrainingJob` row (mode=hpo).
    """
    job_id: str = self.request.id
    settings = get_settings()
    training_uuid = UUID(training_id)

    # Deferred import — Optuna is in base deps but we keep imports inside the
    # task body so module-level import of this file doesn't pay for it.
    import optuna

    with sync_redis_scope() as redis:

        def publish(message: Any) -> None:
            publish_ws_message(redis, job_id, message)

        artifact_uri: str | None = None
        size_bytes: int = 0

        try:
            # ---- 1. Load TrainingJob + Dataset --------------------------------
            with session_scope() as session:
                job = session.get(TrainingJob, training_uuid)
                if job is None:
                    raise RuntimeError(f"TrainingJob {training_id} not found")
                if job.mode.value != "hpo":
                    raise RuntimeError(
                        f"train.hpo called on job with mode={job.mode.value}"
                    )

                dataset = session.get(Dataset, job.dataset_id)
                if dataset is None:
                    raise RuntimeError(f"Dataset {job.dataset_id} not found")
                if not dataset.storage_uri:
                    raise RuntimeError(
                        f"Dataset {dataset.id} has no storage_uri — generation may have failed"
                    )

                base_model = job.base_model
                hpo_config = HPOConfig.model_validate(job.config_json)
                task_type = dataset.task_type
                tool_definitions = _extract_tool_definitions(dataset.generation_metadata)
                dataset_uri = dataset.storage_uri
                training_name = job.training_name or f"hpo-{job_id[:8]}"

                job.status = JobStatus.RUNNING
                job.started_at = datetime.now(timezone.utc)

            # ---- 2. Pull rows --------------------------------------------------
            log.info("hpo: job=%s loading dataset %s", job_id, dataset_uri)
            minio = get_minio_client()
            ds_bucket, ds_key = parse_s3_uri(dataset_uri)
            rows = get_jsonl(minio, ds_bucket, ds_key)
            if not rows:
                raise RuntimeError(f"Dataset {dataset_uri} is empty")

            # ---- 3. Parent MLflow run ------------------------------------------
            workdir = make_optuna_workdir(prefix=f"hpo-{training_id}-")
            try:
                with mlflow_run_scope(
                    experiment_name=f"slm/{task_type.value}",
                    run_name=training_name,
                    tags={
                        "training_id": training_id,
                        "celery_job_id": job_id,
                        "task_type": task_type.value,
                        "base_model": base_model,
                        "mode": "hpo",
                    },
                ) as parent_run:
                    with session_scope() as session:
                        row = session.get(TrainingJob, training_uuid)
                        if row is not None:
                            row.mlflow_run_id = parent_run.run_id
                            row.mlflow_experiment_id = parent_run.experiment_id

                    log_params_flat(
                        {
                            "base_model": base_model,
                            "task_type": task_type.value,
                            "num_samples": len(rows),
                            "n_trials": hpo_config.n_trials,
                            "objective_metric": hpo_config.objective_metric,
                            "direction": hpo_config.direction,
                            "sampler": hpo_config.sampler,
                            "pruner": hpo_config.pruner,
                            "fixed_config": hpo_config.fixed_config.model_dump(mode="json"),
                            "search_space": hpo_config.search_space.model_dump(mode="json"),
                        }
                    )

                    # ---- 4. Run the study ---------------------------------------
                    def on_trial(out: TrialOutcome) -> None:
                        publish(
                            HPOProgress(
                                job_id=job_id,
                                trial_number=out.trial_number,
                                trials_total=out.trials_total,
                                current_params=_coerce_params(out.params),
                                best_value=out.best_value,
                                best_params=_coerce_params(out.best_params or {}),
                                last_trial_value=out.value,
                                last_trial_pruned=out.pruned,
                                inner_progress=None,
                            )
                        )

                    objective = HPOObjective(
                        base_model=base_model,
                        task_type=task_type,
                        rows=rows,
                        hpo_config=hpo_config,
                        workdir_root=workdir,
                        tool_definitions=tool_definitions,
                        on_trial_done=on_trial,
                        # Inner per-step training progress is suppressed during HPO —
                        # the WS firehose would be too chatty across N trials.
                        inner_progress_publish=None,
                        job_id=job_id,
                    )

                    study = optuna.create_study(
                        direction=hpo_config.direction,
                        sampler=_build_sampler(optuna, hpo_config.sampler),
                        pruner=_build_pruner(optuna, hpo_config.pruner),
                        study_name=f"hpo-{training_id}",
                    )
                    study.optimize(
                        objective,
                        n_trials=hpo_config.n_trials,
                        timeout=hpo_config.timeout_seconds,
                        catch=(Exception,),
                    )

                    if not study.best_trial:  # pragma: no cover — guarded by n_trials>=2
                        raise RuntimeError("HPO study finished with no successful trial")

                    best_value = float(study.best_value)
                    best_params = dict(study.best_params)
                    log.info(
                        "hpo: job=%s best %s=%.6f params=%s",
                        job_id,
                        hpo_config.objective_metric,
                        best_value,
                        best_params,
                    )

                    # ---- 5. Final retrain on best params -----------------------
                    final_config = best_params_to_config(best_params, hpo_config.fixed_config)

                    final_workdir = make_optuna_workdir(prefix=f"hpo-final-{training_id}-")
                    try:
                        with mlflow_run_scope(
                            experiment_name=f"slm/{task_type.value}",
                            run_name="best",
                            tags={
                                "training_id": training_id,
                                "celery_job_id": job_id,
                                "mode": "hpo-final",
                            },
                            nested=True,
                        ):
                            log_params_flat(
                                {
                                    "best_params": best_params,
                                    "best_metric_value": best_value,
                                    "config": final_config.model_dump(mode="json"),
                                }
                            )

                            trainer = UnslothTrainer(
                                base_model=base_model,
                                config=final_config,
                                task_type=task_type,
                                tool_definitions=tool_definitions,
                                output_dir=final_workdir,
                            )
                            cb = make_progress_callback(job_id=job_id, publish=publish)
                            final_result = trainer.train(rows, callbacks=[cb])
                            log_metrics_dict(
                                {k: v for k, v in final_result.metrics.items()}
                            )

                            # ---- 6a. Upload adapter ---------------------------
                            artifact_key = f"adapters/{training_id}"
                            file_count, size_bytes = put_directory(
                                minio,
                                settings.minio_models_bucket,
                                artifact_key,
                                final_result.adapter_dir,
                            )
                            artifact_uri = s3_uri(settings.minio_models_bucket, artifact_key)
                            log.info(
                                "hpo: job=%s uploaded %d adapter files (%.1f MB) to %s",
                                job_id,
                                file_count,
                                size_bytes / (1024 * 1024),
                                artifact_uri,
                            )
                    finally:
                        shutil.rmtree(final_workdir, ignore_errors=True)
            finally:
                shutil.rmtree(workdir, ignore_errors=True)

            # ---- 6b. Persist ModelArtifact + outcome on TrainingJob -----------
            artifact_id: UUID | None = None
            with session_scope() as session:
                row = session.get(TrainingJob, training_uuid)
                if row is None:
                    raise RuntimeError(f"TrainingJob {training_id} disappeared mid-run")
                artifact = ModelArtifact(
                    training_job_id=row.id,
                    name=training_name,
                    base_model=base_model,
                    mlflow_run_id=row.mlflow_run_id,
                    lora_adapter_uri=artifact_uri,
                    size_mb=round(size_bytes / (1024 * 1024), 2),
                )
                session.add(artifact)
                session.flush()
                artifact_id = artifact.id

                row.status = JobStatus.COMPLETED
                row.ended_at = datetime.now(timezone.utc)
                row.best_metric_value = best_value
                row.best_params_json = best_params
                audit_service.record(
                    session,
                    action="training.completed",
                    resource_type="training",
                    resource_id=str(row.id),
                    project_id=row.project_id,
                    request_id=request_context.current_request_id(),
                    metadata={
                        "job_id": job_id,
                        "mode": "hpo",
                        "model_artifact_id": str(artifact_id),
                        "best_metric_value": best_value,
                    },
                )

            # ---- 7. Publish JobCompleted -------------------------------------
            publish(
                JobCompleted(
                    job_id=job_id,
                    result={
                        "training_id": training_id,
                        "model_artifact_id": str(artifact_id),
                        "lora_adapter_uri": artifact_uri,
                        "best_metric_value": best_value,
                        "best_params": best_params,
                        "n_trials_completed": len(study.trials),
                    },
                    mlflow_run_id=_load_run_id(training_uuid),
                    model_artifact_id=artifact_id,
                )
            )

            return {
                "status": "completed",
                "training_id": training_id,
                "model_artifact_id": str(artifact_id),
                "lora_adapter_uri": artifact_uri,
                "best_metric_value": best_value,
                "best_params": best_params,
            }

        except BaseException as exc:
            # BaseException, not Exception — see the long note on the same
            # `except` in `workers/tasks/training.py`. A cancel arrives as a
            # SIGTERM that billiard turns into `SystemExit`, which
            # `except Exception` does not catch, so an HPO study cancelled
            # mid-study published no terminal frame at all.
            log.exception("HPO task failed (job=%s)", job_id)
            try:
                with session_scope() as session:
                    row = session.get(TrainingJob, training_uuid)
                    if row is not None:
                        # Don't clobber a CANCELLED the cancel endpoint already set.
                        if row.status != JobStatus.CANCELLED:
                            row.status = JobStatus.FAILED
                        row.ended_at = datetime.now(timezone.utc)
                        row.error_message = (str(exc) or repr(exc))[:4000]
                        audit_service.record(
                            session,
                            action=(
                                "training.cancelled"
                                if row.status == JobStatus.CANCELLED
                                else "training.failed"
                            ),
                            resource_type="training",
                            resource_id=str(row.id),
                            project_id=row.project_id,
                            outcome="failure",
                            request_id=request_context.current_request_id(),
                            metadata={"job_id": job_id, "error_type": type(exc).__name__},
                        )

            except Exception:  # noqa: BLE001
                log.warning("could not persist FAILED for %s", training_id, exc_info=True)
            try:
                publish(
                    JobFailed(
                        job_id=job_id,
                        error=str(exc) or repr(exc),
                        error_type=type(exc).__name__,
                    )
                )
            except Exception:  # noqa: BLE001
                log.warning("failed to publish JobFailed", exc_info=True)
            raise

        finally:
            _release_gpu_memory()


# ---- helpers ---------------------------------------------------------------


def _build_sampler(optuna_mod: Any, name: str) -> Any:
    if name == "tpe":
        return optuna_mod.samplers.TPESampler()
    if name == "random":
        return optuna_mod.samplers.RandomSampler()
    raise ValueError(f"unknown sampler: {name}")


def _build_pruner(optuna_mod: Any, name: str) -> Any:
    if name == "median":
        return optuna_mod.pruners.MedianPruner()
    if name == "none":
        return optuna_mod.pruners.NopPruner()
    raise ValueError(f"unknown pruner: {name}")


def _coerce_params(params: dict[str, Any]) -> dict[str, str | int | float | bool]:
    """Coerce arbitrary param values to the WS schema's allowed types."""
    out: dict[str, str | int | float | bool] = {}
    for k, v in params.items():
        if isinstance(v, (str, int, float, bool)):
            out[k] = v
        else:
            out[k] = str(v)
    return out


def _extract_tool_definitions(metadata: dict[str, Any] | None) -> list | None:
    if not metadata:
        return None
    raw = metadata.get("tool_definitions")
    if not raw:
        return None
    from api.schemas.data_formats import ToolDefinition

    return [ToolDefinition.model_validate(d) for d in raw]


def _load_run_id(training_uuid: UUID) -> str | None:
    with session_scope() as session:
        row = session.get(TrainingJob, training_uuid)
        return row.mlflow_run_id if row else None


def _release_gpu_memory() -> None:
    """Best-effort CUDA cleanup after the whole HPO run."""
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except Exception:  # noqa: BLE001
        log.debug("torch cleanup skipped", exc_info=True)
    gc.collect()


__all__ = ["train_hpo"]
