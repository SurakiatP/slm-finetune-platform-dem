"""Project request / response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from api.schemas.enums import TaskType


class ProjectCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "name": "support-ticket-router",
                    "description": "Classify inbound tickets into one of three queues",
                    "task_type": "classification",
                },
                {
                    "name": "kitchen-tools",
                    "description": "Translate cooking instructions to JSON tool calls",
                    "task_type": "tool_calling",
                },
                {
                    "name": "policy-bot",
                    "description": "Answer questions about our return policy",
                    "task_type": "qa",
                },
            ]
        },
    )

    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    task_type: TaskType
    external_project_id: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "Optional opaque ID from an external system (e.g. a Supabase "
            "project row) to map this Project to 1:1. Omit if not needed. "
            "Must be globally unique across all projects — creating a "
            "second project with an already-used external_project_id "
            "returns 409."
        ),
    )


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    # task_type is intentionally immutable; create a new project to switch tasks.


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    name: str
    description: str | None
    task_type: TaskType
    external_project_id: str | None
    # owner_id is intentionally absent from ProjectCreate/ProjectUpdate above:
    # ownership is derived server-side from the caller's auth token, never
    # accepted as client input. That's a security property, not an oversight.
    #
    # Defaulted to None like the additive fields migration 0006 introduced
    # (DatasetResponse.celery_task_id, ModelArtifactResponse.export_status):
    # without a default Pydantic makes it *required*, which breaks every
    # existing caller that builds this model from a dict rather than from an
    # ORM row. Additive means additive.
    owner_id: str | None = None
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
    created_at: datetime
    updated_at: datetime


__all__ = ["ProjectCreate", "ProjectUpdate", "ProjectResponse"]
