"""Application service for `POST /api/v1/datasets/generate` (Phase 9).

Steps:
  1. Validate the project exists and its `task_type` matches the request.
  2. For with_seed mode: validate `seed_dataset_id` (exists, source=seed,
     task_type matches, project matches; for QA + PDF, ensure pdf_uri set).
  3. Insert a placeholder `Dataset` row (source=sdg, num_samples=0).
  4. Enqueue the SDG Celery task.
  5. Stash the celery task id in the dataset's generation_metadata.
  6. Return `SDGJobAcceptedResponse` for the API contract.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.dataset import Dataset
from api.models.project import Project
from api.schemas.enums import DatasetSource, JobStatus, TaskType
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
    # 1. Project exists + task_type match
    project = await db.get(Project, request.project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {request.project_id} not found",
        )
    if project.task_type != request.task_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Project's task_type is {project.task_type.value} but request "
                f"asks for {request.task_type.value}"
            ),
        )

    # 2. with_seed: validate seed dataset reference
    if isinstance(request, SDGRequestWithSeed):
        await _validate_seed_dataset(db, request, project)

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
            "temperature": request.temperature,
            "requested_samples": request.num_samples,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            **(
                {"seed_dataset_id": str(request.seed_dataset_id)}
                if isinstance(request, SDGRequestWithSeed)
                else {}
            ),
        },
    )
    db.add(dataset)
    await db.flush()  # populate dataset.id

    # 4. Enqueue Celery task
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


async def _validate_seed_dataset(
    db: AsyncSession,
    request: SDGRequestWithSeed,
    project: Project,
) -> None:
    """Confirm the referenced seed dataset exists + matches request expectations."""
    seed = await db.get(Dataset, request.seed_dataset_id)
    if seed is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"seed_dataset_id {request.seed_dataset_id} not found",
        )
    if seed.source != DatasetSource.SEED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Dataset {request.seed_dataset_id} has source={seed.source.value}; "
                "with_seed requires a dataset uploaded via /datasets/upload-seed"
            ),
        )
    if seed.task_type != request.task_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"seed dataset task_type is {seed.task_type.value} but request asks "
                f"for {request.task_type.value}"
            ),
        )
    if seed.project_id != project.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"seed dataset belongs to project {seed.project_id} but request "
                f"is for project {project.id}"
            ),
        )

    meta = seed.generation_metadata or {}
    has_pdf = bool(meta.get("pdf_uri"))
    has_jsonl = bool(seed.storage_uri)

    if not has_pdf and not has_jsonl:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"seed dataset {request.seed_dataset_id} has neither JSONL rows nor "
                "a pdf_uri — cannot be used as a seed"
            ),
        )
    # PDF flow gating: PDF uploads are only allowed for QA.
    if has_pdf and request.task_type != TaskType.QA:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"seed dataset {request.seed_dataset_id} has a pdf_uri but request "
                f"task_type is {request.task_type.value}; PDF seeds are QA-only"
            ),
        )


__all__ = ["submit_sdg_job"]
