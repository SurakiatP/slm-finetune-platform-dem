"""Application service for `/api/v1/evaluations`."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core import request_context
from api.core.auth import CurrentUser
from api.models.evaluation_run import EvaluationRun
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.enums import JobStatus
from api.schemas.evaluations import (
    EvaluationAcceptedResponse,
    EvaluationCompareRequest,
    EvaluationCompareResponse,
    EvaluationCreate,
    EvaluationResponse,
)
from api.schemas.responses import Page
from api.services import audit_service, ownership, quota
from api.services.quota import Bucket
from api.services.job_control import TERMINAL_JOB_STATUSES, revoke_celery_task


async def submit_evaluation_job(
    db: AsyncSession,
    request: EvaluationCreate,
    user: CurrentUser | None = None,
) -> EvaluationAcceptedResponse:
    """Validate, persist `EvaluationRun` row, enqueue worker.

    Both `model_artifact_id` and `dataset_id` are ownership-checked here —
    the parent check on *both* FKs, since "evaluating someone else's model"
    and "evaluating with someone else's dataset" are each independently the
    interesting attack (a caller could own neither, or own one but not the
    other).
    """
    artifact = await ownership.assert_model_access(db, request.model_artifact_id, user)
    if not artifact.ollama_model_tag:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Model {artifact.id} has not been registered with Ollama. "
                f"POST /api/v1/models/{artifact.id}/export with format=gguf first."
            ),
        )

    dataset = await ownership.assert_dataset_access(db, request.dataset_id, user)
    if not dataset.storage_uri or dataset.num_samples == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Dataset {dataset.id} has no rows persisted yet. "
                "Wait for SDG to complete or upload seed data first."
            ),
        )

    training_job = await db.get(TrainingJob, artifact.training_job_id)
    if training_job is not None:
        project = await db.get(Project, training_job.project_id)
        if project is not None and dataset.task_type != project.task_type:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Dataset task_type={dataset.task_type.value} does not match "
                    f"artifact task_type={project.task_type.value}"
                ),
            )

    # GPU quota gate — one bucket shared with training/HPO and export (they
    # all pin the same RTX 3060). After all validation above, right before
    # the row insert.
    await quota.assert_can_submit(db, bucket=Bucket.GPU, actor_id=request_context.current_user_id())

    ev = EvaluationRun(
        model_artifact_id=artifact.id,
        dataset_id=dataset.id,
        status=JobStatus.PENDING,
    )
    db.add(ev)
    await db.flush()

    from workers.tasks.evaluation import run_evaluation

    async_result = run_evaluation.apply_async(
        kwargs={
            "evaluation_id": str(ev.id),
            "use_llm_judge": request.use_llm_judge,
            "judge_model": request.judge_model,
        },
    )
    job_id: str = async_result.id

    ev.celery_task_id = job_id
    ev.started_at = datetime.now(timezone.utc)
    audit_service.record(
        db,
        action="evaluation.submit",
        resource_type="evaluation",
        resource_id=str(ev.id),
        project_id=training_job.project_id if training_job is not None else None,
        actor_id=request_context.current_user_id(),
        request_id=request_context.current_request_id(),
        metadata={"job_id": job_id, "model_artifact_id": str(ev.model_artifact_id), "dataset_id": str(ev.dataset_id)},
    )
    await db.commit()

    return EvaluationAcceptedResponse(
        evaluation_id=ev.id,
        job_id=job_id,
        status=JobStatus.PENDING,
        websocket_url=f"/ws/jobs/{job_id}",
    )


async def list_evaluations(
    db: AsyncSession,
    *,
    model_artifact_id: UUID | None,
    dataset_id: UUID | None,
    status_filter: JobStatus | None,
    limit: int,
    offset: int,
    user: CurrentUser | None = None,
) -> Page[EvaluationResponse]:
    """List evaluation runs, optionally filtered by artifact / dataset / status."""
    base = select(EvaluationRun).order_by(EvaluationRun.created_at.desc())
    count = select(func.count()).select_from(EvaluationRun)
    if model_artifact_id is not None:
        base = base.where(EvaluationRun.model_artifact_id == model_artifact_id)
        count = count.where(EvaluationRun.model_artifact_id == model_artifact_id)
    if dataset_id is not None:
        base = base.where(EvaluationRun.dataset_id == dataset_id)
        count = count.where(EvaluationRun.dataset_id == dataset_id)
    if status_filter is not None:
        base = base.where(EvaluationRun.status == status_filter)
        count = count.where(EvaluationRun.status == status_filter)
    base = ownership.scope_evaluations_to_owner(base, user)
    count = ownership.scope_evaluations_to_owner(count, user)
    total = (await db.execute(count)).scalar_one()
    rows = (await db.execute(base.limit(limit).offset(offset))).scalars().all()
    return Page[EvaluationResponse](
        items=[EvaluationResponse.model_validate(r) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


async def get_evaluation(
    db: AsyncSession,
    evaluation_id: UUID,
    user: CurrentUser | None = None,
) -> EvaluationResponse:
    ev = await ownership.assert_evaluation_access(db, evaluation_id, user)
    return EvaluationResponse.model_validate(ev)


async def cancel_evaluation(
    db: AsyncSession, evaluation_id: UUID, user: CurrentUser | None = None
) -> dict[str, str]:
    """Revoke the underlying Celery task + flip status to CANCELLED.

    Idempotent: cancelling an already-terminal evaluation run returns 200
    with the existing status. Cancelling a non-existent run, or one
    belonging to another user, returns 404.
    """
    ev = await ownership.assert_evaluation_access(db, evaluation_id, user)
    if ev.status in TERMINAL_JOB_STATUSES:
        return {"evaluation_id": str(ev.id), "status": ev.status.value}

    revoke_celery_task(ev.celery_task_id, context=f"evaluation {evaluation_id}")

    ev.status = JobStatus.CANCELLED
    ev.ended_at = datetime.now(timezone.utc)
    audit_service.record(
        db,
        action="evaluation.cancel",
        resource_type="evaluation",
        resource_id=str(ev.id),
        project_id=await _project_id_for_evaluation(db, ev),
        actor_id=request_context.current_user_id(),
        request_id=request_context.current_request_id(),
        metadata={"job_id": ev.celery_task_id},
    )
    await db.commit()
    return {"evaluation_id": str(ev.id), "status": JobStatus.CANCELLED.value}


async def compare_evaluations(
    db: AsyncSession,
    request: EvaluationCompareRequest,
    user: CurrentUser | None = None,
) -> EvaluationCompareResponse:
    """Pivot metrics across N evaluation runs into a `metric → {eval_id → value}` map.

    Missing metrics on any given run are emitted as `None` rather than dropped
    so the frontend can render a complete grid.

    Ownership: the id list is scoped to `user`'s own evaluations the same
    way `list_evaluations` is (`scope_evaluations_to_owner`), so a run
    belonging to another user simply doesn't come back from the query and
    falls into the existing "not found" branch below — no separate 403
    path needed, and no way to distinguish "not yours" from "doesn't exist".
    """
    stmt = ownership.scope_evaluations_to_owner(
        select(EvaluationRun).where(EvaluationRun.id.in_(list(request.evaluation_ids))),
        user,
    )
    rows = (await db.execute(stmt)).scalars().all()
    by_id: dict[UUID, EvaluationRun] = {r.id: r for r in rows}

    missing = [str(eid) for eid in request.evaluation_ids if eid not in by_id]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Evaluations not found: {missing}",
        )

    # Collect the union of metric names across all runs.
    metric_names: set[str] = set()
    for r in rows:
        if isinstance(r.metrics_json, dict):
            for k, v in r.metrics_json.items():
                if isinstance(v, (int, float)):
                    metric_names.add(k)

    metrics_table: dict[str, dict[str, float | None]] = {}
    for name in sorted(metric_names):
        metrics_table[name] = {}
        for eid in request.evaluation_ids:
            r = by_id[eid]
            value = (r.metrics_json or {}).get(name)
            metrics_table[name][str(eid)] = float(value) if isinstance(value, (int, float)) else None

    judge_scores = {
        str(eid): (float(by_id[eid].llm_judge_score) if by_id[eid].llm_judge_score is not None else None)
        for eid in request.evaluation_ids
    }

    return EvaluationCompareResponse(
        evaluation_ids=list(request.evaluation_ids),
        metrics=metrics_table,
        judge_scores=judge_scores,
    )



async def _project_id_for_evaluation(db: AsyncSession, ev: EvaluationRun):
    """Walk EvaluationRun -> ModelArtifact -> TrainingJob -> Project.

    Three hops, the deepest ownership path in the schema (the same one
    `api/services/job_ownership.py` documents). Returns None rather than
    raising if any link is missing: an audit row with a null project is still
    worth keeping, and this is called from a cancel that has already passed
    its own ownership check.
    """
    artifact = await db.get(ModelArtifact, ev.model_artifact_id)
    if artifact is None:
        return None
    training_job = await db.get(TrainingJob, artifact.training_job_id)
    return training_job.project_id if training_job is not None else None


__all__ = [
    "submit_evaluation_job",
    "list_evaluations",
    "get_evaluation",
    "cancel_evaluation",
    "compare_evaluations",
]
