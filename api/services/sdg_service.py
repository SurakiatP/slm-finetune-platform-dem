"""Application service for `POST /api/v1/datasets/generate` (Phase 9).

Steps:
  1. Validate the project exists and its `task_type` matches the request.
  2. For with_seed mode: validate `seed_dataset_id` (exists, source=seed,
     task_type matches, project matches; for QA + PDF, ensure pdf_uri set).
  3. Submit-gate: breaker closed, budget available, quota available (see the
     dedicated comment block at the call site for why this order and why it
     runs before anything is written).
  4. Insert a placeholder `Dataset` row (source=sdg, num_samples=0).
  5. Enqueue the SDG Celery task.
  6. Stash the celery task id in the dataset's generation_metadata.
  7. Return `SDGJobAcceptedResponse` for the API contract.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.core import request_context
from api.models.dataset import Dataset
from api.models.project import Project
from api.schemas.enums import DatasetSource, JobStatus, TaskType
from api.schemas.sdg import (
    SDGJobAcceptedResponse,
    SDGRequestDescriptionOnly,
    SDGRequestWithSeed,
)
from api.services import audit_service, circuit_breaker, quota, usage_service
from api.services.quota import Bucket


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

    # 3. Submit gate: three independent checks, all keyed on the caller, run
    # in this specific order and — critically — all of them BEFORE any row
    # is written below. If a rejected submit had already inserted the
    # placeholder Dataset row, that `pending` row would itself count toward
    # the very SDG-quota check that just rejected it, so the next legitimate
    # submit would be one job closer to a 429 for no reason. No writes above
    # this line either (project/seed validation are read-only), so a 503/
    # 402/429 here always leaves the DB exactly as it was before the call.
    #
    # Ordering, cheapest/most-certain first:
    #   a) circuit breaker — OpenRouter being down is a platform-wide fact,
    #      true for every actor and every request; checking it first refuses
    #      outright rather than making the caller wait ~20 minutes for the
    #      worker to discover the same thing via tenacity retries.
    #   b) budget — a hard financial stop. More specific than the breaker
    #      (per-actor/global spend, not "is the provider reachable"), but
    #      still not going to change moment-to-moment the way in-flight job
    #      counts do.
    #   c) quota — the softest and most transient of the three: an in-flight
    #      count that a single other job finishing can flip back under the
    #      limit, so it's the most likely to be a false rejection and the
    #      cheapest to leave for last.
    actor_id = request_context.current_user_id()
    await circuit_breaker.assert_closed()
    await usage_service.assert_within_budget(db, actor_id=actor_id)
    await quota.assert_can_submit(db, bucket=Bucket.SDG, actor_id=actor_id)

    # 4. Create placeholder Dataset row
    name = request.dataset_name or f"sdg-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    dataset = Dataset(
        project_id=request.project_id,
        name=name,
        task_type=request.task_type,
        source=DatasetSource.SDG,
        status=JobStatus.PENDING,
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

    # 5. Enqueue Celery task
    from workers.tasks.data_generation import generate_synthetic_data

    # Full request dump — dataset_name and holdout_name both ride along here
    # as ordinary schema fields (no special-casing needed); the worker reads
    # `request.holdout_name` the same way it reads `request.dataset_name`.
    payload = request.model_dump(mode="json")
    async_result = generate_synthetic_data.apply_async(
        kwargs={"request_payload": payload, "dataset_id": str(dataset.id)},
    )
    job_id: str = async_result.id

    # 6. Persist celery_task_id as a first-class column + commit. The JSONB
    # metadata key is kept in sync too (not replaced) — docs/03 §2 documents
    # that path and existing clients may still read it from there.
    metadata = dict(dataset.generation_metadata or {})
    metadata["celery_task_id"] = job_id
    dataset.generation_metadata = metadata
    dataset.celery_task_id = job_id
    # Same transaction as the task id: if the audit row cannot be written the
    # submission is not recorded as having happened either.
    audit_service.record(
        db,
        action="sdg.submit",
        resource_type="dataset",
        resource_id=str(dataset.id),
        project_id=request.project_id,
        actor_id=request_context.current_user_id(),
        request_id=request_context.current_request_id(),
        metadata={
            "job_id": job_id,
            "sdg_mode": request.sdg_mode.value,
            "num_samples": request.num_samples,
            "task_type": request.task_type.value,
        },
    )
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
                # See training_service.py's twin of this message: the other
                # project's UUID is deliberately omitted rather than echoed.
                f"seed dataset {seed.id} belongs to a different project but "
                f"the request is for project {project.id}"
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
