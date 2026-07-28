"""Project request / response schemas."""

from __future__ import annotations

from datetime import datetime
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
    created_at: datetime
    updated_at: datetime


__all__ = ["ProjectCreate", "ProjectUpdate", "ProjectResponse"]
