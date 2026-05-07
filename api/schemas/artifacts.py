"""Trained-model artifact schemas (the `models/` REST resource).

Named `artifacts` to avoid shadowing the ORM package `api.models`.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from api.schemas.enums import ArtifactFormat, JobStatus


class ModelArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    training_job_id: UUID
    name: str
    base_model: str
    mlflow_run_id: str | None
    lora_adapter_uri: str | None
    gguf_uri: str | None
    safetensors_uri: str | None
    size_mb: float | None
    ollama_model_tag: str | None
    created_at: datetime
    updated_at: datetime


class ModelExportRequest(BaseModel):
    """Body for `POST /api/v1/models/{id}/export`."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"format": "gguf", "quantization": "q4_k_m"},
                {"format": "safetensors"},
            ]
        },
    )

    format: ArtifactFormat
    quantization: str | None = None  # e.g. "q4_k_m" for GGUF; ignored for safetensors


class ModelExportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID
    format: ArtifactFormat
    job_id: str
    status: JobStatus
    websocket_url: str


__all__ = ["ModelArtifactResponse", "ModelExportRequest", "ModelExportResponse"]
