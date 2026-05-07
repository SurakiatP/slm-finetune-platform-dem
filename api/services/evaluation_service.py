"""Application service for `/api/v1/evaluations`."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.evaluation_run import EvaluationRun
from api.models.model_artifact import ModelArtifact
from api.models.dataset import Dataset
from api.schemas.enums import JobStatus
from api.schemas.evaluations import (
    EvaluationAcceptedResponse,
    EvaluationCompareRequest,
    EvaluationCompareResponse,
    EvaluationCreate,
    EvaluationResponse,
)


async def submit_evaluation_job(
    db: AsyncSession,
    request: EvaluationCreate,
) -> EvaluationAcceptedResponse:
    """Validate, persist `EvaluationRun` row, enqueue worker."""
    artifact = await db.get(ModelArtifact, request.model_artifact_id)
    if artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model {request.model_artifact_id} not found",
        )
    if not artifact.ollama_model_tag:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Model {artifact.id} has not been registered with Ollama. "
                f"POST /api/v1/models/{artifact.id}/export with format=gguf first."
            ),
        )

    dataset = await db.get(Dataset, request.dataset_id)
    if dataset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dataset {request.dataset_id} not found",
        )
    if not dataset.storage_uri or dataset.num_samples == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Dataset {dataset.id} has no rows persisted yet. "
                "Wait for SDG to complete or upload seed data first."
            ),
        )

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
    await db.commit()

    return EvaluationAcceptedResponse(
        evaluation_id=ev.id,
        job_id=job_id,
        status=JobStatus.PENDING,
        websocket_url=f"/ws/jobs/{job_id}",
    )


async def get_evaluation(
    db: AsyncSession,
    evaluation_id: UUID,
) -> EvaluationResponse:
    ev = await db.get(EvaluationRun, evaluation_id)
    if ev is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Evaluation {evaluation_id} not found",
        )
    return EvaluationResponse.model_validate(ev)


async def compare_evaluations(
    db: AsyncSession,
    request: EvaluationCompareRequest,
) -> EvaluationCompareResponse:
    """Pivot metrics across N evaluation runs into a `metric → {eval_id → value}` map.

    Missing metrics on any given run are emitted as `None` rather than dropped
    so the frontend can render a complete grid.
    """
    rows = (
        await db.execute(
            select(EvaluationRun).where(EvaluationRun.id.in_(list(request.evaluation_ids)))
        )
    ).scalars().all()
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


__all__ = [
    "submit_evaluation_job",
    "get_evaluation",
    "compare_evaluations",
]
