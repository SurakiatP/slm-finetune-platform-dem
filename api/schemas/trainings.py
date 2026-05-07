"""Training read schemas — list / detail / mlflow url.

The request side (`TrainingRequest`) and the accepted-job response live in
`api/schemas/training.py`. This file is read-only views.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
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


class MlflowUrlResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    training_id: UUID
    mlflow_run_id: str | None
    mlflow_url: str | None


__all__ = ["TrainingResponse", "MlflowUrlResponse"]
