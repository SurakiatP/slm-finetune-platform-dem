"""Application service for `POST /api/v1/datasets/generate`.

Steps:
  1. Validate the project exists and its `task_type` matches the request.
  2. Insert a placeholder `Dataset` row (source=sdg, num_samples=0).
  3. Enqueue the SDG Celery task.
  4. Stash the celery task id in the dataset's generation_metadata.
  5. Return `SDGJobAcceptedResponse` for the API contract.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.dataset import Dataset
from api.models.project import Project
from api.schemas.enums import DatasetSource, JobStatus
from api.schemas.sdg import (
    SDGJobAcceptedResponse,
    SDGRequestDescriptionOnly,
    SDGRequestWithSeed,
)


async def submit_sdg_job(
    db: AsyncSession,
    request: SDGRequestWithSeed | SDGRequestDescriptionOnly,
) -> SDGJobAcceptedResponse:
    """Validate, persist the placeholder dataset, and enqueue the worker task."""
    # 1. Project exists?
    project = await db.get(Project, request.project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {request.project_id} not found",
        )
    # 2. task_type matches the project's
    if project.task_type != request.task_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Project's task_type is {project.task_type.value} but request "
                f"asks for {request.task_type.value}"
            ),
        )

    # 3. Create placeholder Dataset row
    name = request.dataset_name or f"sdg-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    dataset = Dataset(
        project_id=request.project_id,
        name=name,
        task_type=request.task_type,
        source=DatasetSource.SDG,
        num_samples=0,
        generation_metadata={
            "sdg_mode": request.sdg_mode.value,
            "task_description": request.task_description,
            "teacher_model": request.teacher_model,
            "temperature": request.temperature,
            "requested_samples": request.num_samples,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    db.add(dataset)
    await db.flush()  # populate dataset.id

    # 4. Enqueue Celery task. Local import keeps the API process from
    # eagerly loading worker-only deps (minio, etc.) at module import time.
    from workers.tasks.data_generation import generate_synthetic_data

    payload = request.model_dump(mode="json")
    async_result = generate_synthetic_data.apply_async(
        kwargs={"request_payload": payload, "dataset_id": str(dataset.id)},
    )
    job_id: str = async_result.id

    # 5. Persist celery_task_id in metadata + commit
    metadata = dict(dataset.generation_metadata or {})
    metadata["celery_task_id"] = job_id
    dataset.generation_metadata = metadata
    await db.commit()

    return SDGJobAcceptedResponse(
        job_id=job_id,
        dataset_id=dataset.id,
        status=JobStatus.PENDING,
        websocket_url=f"/ws/jobs/{job_id}",
    )


__all__ = ["submit_sdg_job"]
