"""Dataset response / preview schemas (request side lives in `sdg.py`)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from api.schemas.enums import DatasetSource, JobStatus, TaskType


class DatasetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    project_id: UUID
    parent_dataset_id: UUID | None = Field(
        default=None,
        description=(
            "If set, this dataset is a holdout child of another dataset (created "
            "by SDG over-generation). Use the parent for training and this one "
            "for `POST /evaluations` to get a leak-free judge score."
        ),
    )
    name: str
    task_type: TaskType
    source: DatasetSource
    status: JobStatus = Field(
        ...,
        description=(
            "Lifecycle of dataset population: pending (SDG queued) / running "
            "(SDG worker executing) / completed (rows persisted, ready to use) / "
            "failed (see error_message) / cancelled."
        ),
    )
    error_message: str | None = Field(
        default=None,
        description="Populated when status=failed — the SDG worker's exception message.",
    )
    num_samples: int
    storage_uri: str | None
    size_bytes: int | None
    generation_metadata: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class DatasetPreviewResponse(BaseModel):
    """Body of `GET /datasets/{id}/preview?limit=N`."""

    model_config = ConfigDict(extra="forbid")

    dataset_id: UUID
    task_type: TaskType
    samples: list[dict[str, Any]] = Field(
        ...,
        description="First N rows; row shape matches data_formats.<TaskType>Sample.",
    )
    total: int


__all__ = ["DatasetResponse", "DatasetPreviewResponse"]
