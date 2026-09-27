"""Deployment request / response schemas.

A Deployment is a logical resource on the shared Ollama instance (preload =
`keep_alive=-1`, stop = `keep_alive=0`) — see api/services/deployments_service.py
for the state machine. Status reuses `JobStatus`: pending -> running (=
active) -> completed (= stopped) / failed / cancelled.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from api.schemas.enums import JobStatus


class DeploymentCreate(BaseModel):
    """Body for `POST /api/v1/deployments`."""

    model_config = ConfigDict(extra="forbid")

    model_artifact_id: UUID
    name: str | None = Field(default=None, min_length=1, max_length=200)


class DeploymentUpdate(BaseModel):
    """Body for `PATCH /api/v1/deployments/{id}`."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    rate_limit_per_min: int | None = Field(default=None, ge=1)


class DeploymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    name: str
    model_artifact_id: UUID | None
    # The artifact's `ollama_model_tag` at read time (outer-joined in, not a
    # stored column on Deployment) — null once the artifact is gone
    # (ondelete SET NULL) or was never exported to Ollama.
    model_tag: str | None
    status: JobStatus
    rate_limit_per_min: int
    job_id: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


__all__ = ["DeploymentCreate", "DeploymentResponse", "DeploymentUpdate"]
