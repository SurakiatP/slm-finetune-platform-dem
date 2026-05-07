"""Schemas for the static metadata endpoints.

`GET /api/v1/tasks` and `GET /api/v1/base-models` exist so the frontend can
build dynamic forms without hard-coding our supported task types or models.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from api.schemas.enums import TaskType


class TaskTypeInfo(BaseModel):
    """One supported task type as exposed via `GET /api/v1/tasks`."""

    model_config = ConfigDict(extra="forbid")

    task_type: TaskType
    display_name: str
    description: str
    sample_schema: dict[str, Any] = Field(
        ...,
        description="JSON Schema (Pydantic-generated) of one row of training data.",
    )
    example: dict[str, Any] = Field(..., description="A canonical example row.")
    sdg_modes_supported: list[str] = Field(
        default_factory=lambda: ["with_seed", "description_only"],
    )


class BaseModelInfo(BaseModel):
    """One Unsloth 4-bit base model that fits the RTX 3060 12 GB target."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="HuggingFace model id, e.g. 'unsloth/...'.")
    display_name: str
    family: str = Field(..., description="'llama' / 'qwen' / 'gemma' / ...")
    params_billions: float = Field(..., gt=0.0, le=3.5)
    context_length: int = Field(..., ge=512)
    recommended_max_seq_length: int = Field(..., ge=128)
    quantization: str = "bnb-4bit"
    license: str | None = None
    notes: str | None = None


__all__ = ["TaskTypeInfo", "BaseModelInfo"]
