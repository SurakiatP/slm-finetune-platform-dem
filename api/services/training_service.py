"""Application service for `POST /api/v1/trainings`.

Steps:
  1. Validate project exists.
  2. Validate dataset exists, belongs to the project, has finished generation
     (storage_uri set) and matches the project's task_type.
  3. Validate base_model is in the supported allowlist (ADR-002).
  4. Insert TrainingJob row (status=PENDING, status flips to RUNNING in the worker).
  5. Enqueue the train.manual Celery task.
  6. Stash celery_task_id back on the row.
  7. Return TrainingJobAcceptedResponse.

HPO mode goes through `submit_hpo_training_job` (Phase 6 — currently 501).
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings
from api.models.dataset import Dataset
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.routers.tasks_meta import SUPPORTED_BASE_MODELS
from api.schemas.enums import JobStatus
from api.schemas.training import (
    HPOTrainingRequest,
    ManualTrainingRequest,
    TrainingJobAcceptedResponse,
)

_SUPPORTED_MODEL_IDS: frozenset[str] = frozenset(m.id for m in SUPPORTED_BASE_MODELS)


async def submit_manual_training_job(
    db: AsyncSession,
    request: ManualTrainingRequest,
) -> TrainingJobAcceptedResponse:
    """Validate the request, persist the TrainingJob row, and enqueue the worker."""
    settings = get_settings()

    # 1. Project exists?
    project = await db.get(Project, request.project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {request.project_id} not found",
        )

    # 2. Dataset exists + ready + matches project?
    dataset = await db.get(Dataset, request.dataset_id)
    if dataset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dataset {request.dataset_id} not found",
        )
    if dataset.project_id != project.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Dataset {dataset.id} belongs to a different project "
                f"({dataset.project_id}); cannot use it for training in {project.id}"
            ),
        )
    if dataset.task_type != project.task_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Dataset task_type ({dataset.task_type.value}) does not match "
                f"project task_type ({project.task_type.value})"
            ),
        )
    if not dataset.storage_uri or dataset.num_samples == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Dataset {dataset.id} is not ready (no rows persisted yet). "
                "Wait for SDG to complete or upload seed data first."
            ),
        )

    # 3. Base model allowlist (ADR-002 — no arbitrary HF models)
    base_model = request.base_model or settings.default_base_model
    if base_model not in _SUPPORTED_MODEL_IDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"base_model '{base_model}' is not in the supported list. "
                f"Use GET /api/v1/base-models to see allowed values."
            ),
        )

    # 4. Insert TrainingJob row.
    job_row = TrainingJob(
        project_id=project.id,
        dataset_id=dataset.id,
        mode=request.mode,
        status=JobStatus.PENDING,
        base_model=base_model,
        training_name=request.training_name,
        config_json=request.manual_config.model_dump(mode="json"),
    )
    db.add(job_row)
    await db.flush()  # populate job_row.id

    # 5. Enqueue Celery task. Local import keeps the API process from
    # eagerly loading worker-only deps (torch, unsloth, minio, ...).
    from workers.tasks.training import train_manual

    async_result = train_manual.apply_async(
        kwargs={"training_id": str(job_row.id)},
    )
    job_id: str = async_result.id

    # 6. Persist celery_task_id and commit.
    job_row.celery_task_id = job_id
    await db.commit()

    # 7. Return.
    return TrainingJobAcceptedResponse(
        job_id=job_id,
        training_id=job_row.id,
        mlflow_run_id=None,        # populated by the worker once the run starts
        mlflow_url=None,
        status=JobStatus.PENDING,
        websocket_url=f"/ws/jobs/{job_id}",
    )


async def submit_hpo_training_job(
    db: AsyncSession,
    request: HPOTrainingRequest,
) -> TrainingJobAcceptedResponse:
    """Validate the HPO request, persist the TrainingJob row, and enqueue the worker.

    Mirrors `submit_manual_training_job` but persists `hpo_config` (n_trials,
    objective_metric, search_space, fixed_config, ...) into `config_json` and
    dispatches to the `train.hpo` Celery task.
    """
    settings = get_settings()

    # 1. Project exists?
    project = await db.get(Project, request.project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {request.project_id} not found",
        )

    # 2. Dataset exists + ready + matches project?
    dataset = await db.get(Dataset, request.dataset_id)
    if dataset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dataset {request.dataset_id} not found",
        )
    if dataset.project_id != project.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Dataset {dataset.id} belongs to a different project "
                f"({dataset.project_id}); cannot use it for training in {project.id}"
            ),
        )
    if dataset.task_type != project.task_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Dataset task_type ({dataset.task_type.value}) does not match "
                f"project task_type ({project.task_type.value})"
            ),
        )
    if not dataset.storage_uri or dataset.num_samples == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Dataset {dataset.id} is not ready (no rows persisted yet). "
                "Wait for SDG to complete or upload seed data first."
            ),
        )

    # 3. Base model allowlist (ADR-002)
    base_model = request.base_model or settings.default_base_model
    if base_model not in _SUPPORTED_MODEL_IDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"base_model '{base_model}' is not in the supported list. "
                f"Use GET /api/v1/base-models to see allowed values."
            ),
        )

    # 4. Per-config trial cap (defense-in-depth on top of HPOConfig's own cap).
    if request.hpo_config.n_trials > settings.default_hpo_max_trials:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"hpo_config.n_trials={request.hpo_config.n_trials} exceeds the platform cap "
                f"({settings.default_hpo_max_trials}). Lower it or raise DEFAULT_HPO_MAX_TRIALS."
            ),
        )

    # 5. Insert TrainingJob row.
    job_row = TrainingJob(
        project_id=project.id,
        dataset_id=dataset.id,
        mode=request.mode,
        status=JobStatus.PENDING,
        base_model=base_model,
        training_name=request.training_name,
        config_json=request.hpo_config.model_dump(mode="json"),
    )
    db.add(job_row)
    await db.flush()

    # 6. Enqueue Celery task. Local import keeps the API process from
    # eagerly loading worker-only deps.
    from workers.tasks.hpo_training import train_hpo

    async_result = train_hpo.apply_async(
        kwargs={"training_id": str(job_row.id)},
    )
    job_id: str = async_result.id

    job_row.celery_task_id = job_id
    await db.commit()

    return TrainingJobAcceptedResponse(
        job_id=job_id,
        training_id=job_row.id,
        mlflow_run_id=None,
        mlflow_url=None,
        status=JobStatus.PENDING,
        websocket_url=f"/ws/jobs/{job_id}",
    )


__all__ = ["submit_manual_training_job", "submit_hpo_training_job"]
