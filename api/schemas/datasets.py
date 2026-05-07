"""Dataset response / preview schemas (request side lives in `sdg.py`)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from api.schemas.enums import DatasetSource, TaskType


class DatasetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    project_id: UUID
    name: str
    task_type: TaskType
    source: DatasetSource
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
