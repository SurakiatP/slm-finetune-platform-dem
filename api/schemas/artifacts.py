"""Trained-model artifact schemas (the `models/` REST resource).

Named `artifacts` to avoid shadowing the ORM package `api.models`.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, computed_field

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
    export_error_message: str | None
    export_status: JobStatus | None = None
    export_celery_task_id: str | None = None
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def base_ollama_tag(self) -> str | None:
        """Ollama-Hub equivalent of `base_model` for A/B compare in playground.

        Lazy import keeps `api.schemas` free of `api.services` dependencies at
        module load time (avoids circular import when artifacts.py is imported
        early during app startup).
        """
        from api.services.base_model_catalog import get_ollama_base_tag

        return get_ollama_base_tag(self.base_model)


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
