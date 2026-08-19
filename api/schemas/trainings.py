"""Training read schemas — list / detail / mlflow url.

The request side (`TrainingRequest`) and the accepted-job response live in
`api/schemas/training.py`. This file is read-only views.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from api.schemas.enums import JobStatus, TrainingMode


class TrainingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    project_id: UUID
    dataset_id: UUID
    mode: TrainingMode
    status: JobStatus
    celery_task_id: str | None
    base_model: str
    training_name: str | None
    mlflow_experiment_id: str | None
    mlflow_run_id: str | None
    config_json: dict[str, Any]
    best_metric_value: float | None
    best_params_json: dict[str, Any] | None
    error_message: str | None
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # Auto-pipeline (auto-export + auto-evaluate after training completes).
    # `auto_pipeline` is null until the pipeline is kicked off; once running
    # it holds {"export": {...}, "evaluate": {...}} per-stage progress — see
    # `api.models.training_job.TrainingJob.auto_pipeline`'s doc for the exact
    # shape of each stage dict.
    auto_export: bool
    auto_evaluate: bool
    auto_pipeline: dict[str, Any] | None = None
    # queue_state / queue_position / owner_queue_position: project-level GPU
    # queue standing (train/export/eval jobs share one GPU, worker
    # concurrency=1). "processing" = a GPU job for this project is currently
    # running (positions are null in that case); "queued" = a GPU job for
    # this project is only pending. queue_position is the 1-based global
    # FIFO ordinal across all queued projects; owner_queue_position is the
    # ordinal within the same owner's queued projects only. Populated only
    # on detail GETs while a job is in-flight — deliberately left null on
    # list endpoints (would require an N+1 queue lookup per row) and on
    # terminal/idle rows. Pure additive, backward compatible.
    queue_state: Literal["processing", "queued"] | None = None
    queue_position: int | None = None
    owner_queue_position: int | None = None


class MlflowUrlResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    training_id: UUID
    mlflow_run_id: str | None
    mlflow_url: str | None


class MetricPoint(BaseModel):
    """One data point of a logged metric series."""

    model_config = ConfigDict(extra="forbid")

    step: int
    value: float
    timestamp_ms: int


class HpoChildSummary(BaseModel):
    """Compact view of one HPO trial run (no full series — just final + params)."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    name: str
    final_eval_loss: float | None
    params: dict[str, str]


class TrainingMetricsResponse(BaseModel):
    """Full metric history of a training run, plus HPO child summary if applicable.

    `metrics` is keyed by MLflow metric name (e.g. `train_loss`, `eval_loss`,
    `learning_rate`). Each value is a list of points sorted by step ascending.
    `hpo_children` is `None` for manual mode and a list for HPO mode.
    """

    model_config = ConfigDict(extra="forbid")

    training_id: UUID
    mlflow_run_id: str | None
    metrics: dict[str, list[MetricPoint]]
    hpo_children: list[HpoChildSummary] | None


class TrainingLossHistoryResponse(BaseModel):
    """Lightweight loss-only payload — meant for chart components.

    Only `train_loss` and `eval_loss` series are populated. Empty lists are
    returned when MLflow has no data for the metric (e.g. eval split=0,
    training failed before first eval, etc.) so the frontend can render an
    empty axis without special-casing nulls.
    """

    model_config = ConfigDict(extra="forbid")

    training_id: UUID
    mlflow_run_id: str | None
    train_loss: list[MetricPoint]
    eval_loss: list[MetricPoint]


__all__ = [
    "TrainingResponse",
    "MlflowUrlResponse",
    "MetricPoint",
    "HpoChildSummary",
    "TrainingMetricsResponse",
    "TrainingLossHistoryResponse",
]
