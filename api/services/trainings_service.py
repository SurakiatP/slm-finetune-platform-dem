"""Application service for `/api/v1/trainings` (read + cancel + mlflow-url).

The submission side (manual + HPO) lives in `training_service.py` (Phase 5/6).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

import httpx
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings
from api.models.training_job import TrainingJob
from api.schemas.enums import JobStatus, TrainingMode
from api.schemas.responses import Page
from api.schemas.trainings import (
    HpoChildSummary,
    MetricPoint,
    MlflowUrlResponse,
    TrainingLossHistoryResponse,
    TrainingMetricsResponse,
    TrainingResponse,
)
from api.services import mlflow_metrics

log = logging.getLogger(__name__)


async def list_trainings(
    db: AsyncSession,
    *,
    project_id: UUID | None,
    status_filter: JobStatus | None,
    limit: int,
    offset: int,
) -> Page[TrainingResponse]:
    base = select(TrainingJob).order_by(TrainingJob.created_at.desc())
    count = select(func.count()).select_from(TrainingJob)
    if project_id is not None:
        base = base.where(TrainingJob.project_id == project_id)
        count = count.where(TrainingJob.project_id == project_id)
    if status_filter is not None:
        base = base.where(TrainingJob.status == status_filter)
        count = count.where(TrainingJob.status == status_filter)
    total = (await db.execute(count)).scalar_one()
    rows = (await db.execute(base.limit(limit).offset(offset))).scalars().all()
    return Page[TrainingResponse](
        items=[TrainingResponse.model_validate(r) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


async def get_training(db: AsyncSession, training_id: UUID) -> TrainingResponse:
    job = await db.get(TrainingJob, training_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Training {training_id} not found",
        )
    return TrainingResponse.model_validate(job)


async def cancel_training(db: AsyncSession, training_id: UUID) -> dict[str, str]:
    """Revoke the underlying Celery task + flip status to CANCELLED.

    Idempotent: cancelling an already-terminal job returns 200 with the existing
    status. Cancelling a non-existent job returns 404.
    """
    job = await db.get(TrainingJob, training_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Training {training_id} not found",
        )
    if job.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
        return {"training_id": str(job.id), "status": job.status.value}

    if job.celery_task_id:
        try:
            # Local import — Celery is in base deps but this keeps test-only
            # imports tidy.
            from workers.celery_app import celery_app

            celery_app.control.revoke(job.celery_task_id, terminate=True, signal="SIGTERM")
        except Exception:  # noqa: BLE001 — proceed to flip status even on broker hiccup
            log.warning(
                "could not revoke celery task %s for training %s",
                job.celery_task_id,
                training_id,
                exc_info=True,
            )

    job.status = JobStatus.CANCELLED
    job.ended_at = datetime.now(timezone.utc)
    await db.commit()
    return {"training_id": str(job.id), "status": JobStatus.CANCELLED.value}


async def get_mlflow_url(db: AsyncSession, training_id: UUID) -> MlflowUrlResponse:
    job = await db.get(TrainingJob, training_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Training {training_id} not found",
        )
    settings = get_settings()
    url: str | None = None
    if job.mlflow_run_id and job.mlflow_experiment_id:
        base = str(
            settings.mlflow_public_url or settings.mlflow_tracking_uri
        ).rstrip("/")
        url = f"{base}/#/experiments/{job.mlflow_experiment_id}/runs/{job.mlflow_run_id}"
    return MlflowUrlResponse(
        training_id=job.id,
        mlflow_run_id=job.mlflow_run_id,
        mlflow_url=url,
    )


async def _load_training_or_404(db: AsyncSession, training_id: UUID) -> TrainingJob:
    job = await db.get(TrainingJob, training_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Training {training_id} not found",
        )
    return job


def _to_points(rows: list[mlflow_metrics.MetricPointDC]) -> list[MetricPoint]:
    """Convert raw MLflow points to API schema, sorted + de-duped.

    Two normalisations:
      1. Sort by step ascending — MLflow may return points out of order when
         steps were logged asynchronously; the frontend wants plot-ready data.
      2. De-dupe by ``(step, value)`` — HF Trainer logs end-of-epoch eval
         multiple times with identical values (initial + final + train-summary
         aliased), and the frontend chart shouldn't render multiple dots on
         top of each other. Different values at the same step are kept (real
         info, e.g. re-evaluation after checkpoint).
    """
    seen: set[tuple[int, float]] = set()
    out: list[MetricPoint] = []
    for p in sorted(rows, key=lambda r: r.step):
        key = (p.step, p.value)
        if key in seen:
            continue
        seen.add(key)
        out.append(MetricPoint(step=p.step, value=p.value, timestamp_ms=p.timestamp_ms))
    return out


async def get_training_loss_history(
    db: AsyncSession, training_id: UUID
) -> TrainingLossHistoryResponse:
    """Return only `train_loss` + `eval_loss` series — small payload for charts."""
    job = await _load_training_or_404(db, training_id)
    if not job.mlflow_run_id:
        return TrainingLossHistoryResponse(
            training_id=job.id,
            mlflow_run_id=None,
            train_loss=[],
            eval_loss=[],
        )

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            train_rows, eval_rows = await asyncio.gather(
                mlflow_metrics.get_metric_history(job.mlflow_run_id, "loss", client=client),
                mlflow_metrics.get_metric_history(job.mlflow_run_id, "eval_loss", client=client),
            )
        except httpx.HTTPError as exc:
            log.warning("loss-history MLflow call failed for training=%s: %s", training_id, exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="MLflow tracking server not reachable",
            ) from exc

    return TrainingLossHistoryResponse(
        training_id=job.id,
        mlflow_run_id=job.mlflow_run_id,
        train_loss=_to_points(train_rows),
        eval_loss=_to_points(eval_rows),
    )


async def get_training_metrics(
    db: AsyncSession, training_id: UUID
) -> TrainingMetricsResponse:
    """Return all logged metric series for the run + HPO child summary if HPO mode.

    Internally: `runs/get` once → metric key list → fan-out `metrics/get-history`
    in parallel. For HPO trainings, also `runs/search` for nested children and
    summarise them (final eval_loss + params, not full series — keeps payload
    bounded when n_trials is large).
    """
    job = await _load_training_or_404(db, training_id)
    if not job.mlflow_run_id:
        return TrainingMetricsResponse(
            training_id=job.id,
            mlflow_run_id=None,
            metrics={},
            hpo_children=None,
        )

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            run = await mlflow_metrics.get_run(job.mlflow_run_id, client=client)
        except httpx.HTTPError as exc:
            log.warning("runs/get failed for training=%s: %s", training_id, exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="MLflow tracking server not reachable",
            ) from exc

        metric_keys = mlflow_metrics.extract_metric_keys(run)
        history_results = await asyncio.gather(
            *(
                mlflow_metrics.get_metric_history(job.mlflow_run_id, key, client=client)
                for key in metric_keys
            ),
            return_exceptions=True,
        )

        metrics: dict[str, list[MetricPoint]] = {}
        for key, result in zip(metric_keys, history_results):
            if isinstance(result, BaseException):
                log.warning("get-history failed key=%s: %s", key, result)
                metrics[key] = []
            else:
                metrics[key] = _to_points(result)

        hpo_children: list[HpoChildSummary] | None = None
        if job.mode is TrainingMode.HPO and job.mlflow_experiment_id:
            try:
                child_runs = await mlflow_metrics.search_child_runs(
                    job.mlflow_run_id, job.mlflow_experiment_id, client=client
                )
            except httpx.HTTPError as exc:
                log.warning("hpo children search failed for training=%s: %s", training_id, exc)
                child_runs = []

            hpo_children = []
            for cr in child_runs:
                name = mlflow_metrics.extract_tag(cr, "mlflow.runName") or ""
                hpo_children.append(
                    HpoChildSummary(
                        run_id=cr.get("info", {}).get("run_id", ""),
                        name=name,
                        final_eval_loss=mlflow_metrics.extract_metric_last_value(cr, "eval_loss"),
                        params=mlflow_metrics.extract_params(cr),
                    )
                )

    return TrainingMetricsResponse(
        training_id=job.id,
        mlflow_run_id=job.mlflow_run_id,
        metrics=metrics,
        hpo_children=hpo_children,
    )


__all__ = [
    "list_trainings",
    "get_training",
    "cancel_training",
    "get_mlflow_url",
    "get_training_loss_history",
    "get_training_metrics",
]
