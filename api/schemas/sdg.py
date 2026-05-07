"""Synthetic Data Generation (SDG) request / response schemas.

`POST /api/v1/datasets/generate` accepts an `SDGRequest` and returns an
`SDGJobAcceptedResponse`. The actual generation runs as a Celery task and
publishes progress to the WebSocket channel `job:{job_id}` (see schemas/progress.py).

Validation rules (from require.md):
  • mode=with_seed         → seed_data: 5–50 rows, each matching task_type's shape
  • mode=description_only  → task-specific config required:
        - classification → classification_config.labels (≥2)
        - tool_calling   → tool_calling_config.tool_definitions (≥1)
        - qa             → no extra config
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from api.schemas.data_formats import ToolDefinition, parse_samples
from api.schemas.enums import JobStatus, SDGMode, TaskType

# --- Per-task generation configs (description_only mode) --------------------


class ClassificationGenConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    labels: list[str] = Field(
        ...,
        min_length=2,
        description="Closed set of class labels the synthetic data must use.",
    )

    @model_validator(mode="after")
    def _labels_unique_nonempty(self) -> Self:
        if any(not lbl.strip() for lbl in self.labels):
            raise ValueError("labels cannot contain empty strings")
        if len(set(self.labels)) != len(self.labels):
            raise ValueError("labels must be unique")
        return self


class ToolCallingGenConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_definitions: list[ToolDefinition] = Field(
        ...,
        min_length=1,
        description="Tools the synthetic samples may invoke.",
    )

    @model_validator(mode="after")
    def _tool_names_unique(self) -> Self:
        names = [t.name for t in self.tool_definitions]
        if len(set(names)) != len(names):
            raise ValueError("tool_definitions names must be unique")
        return self


# --- SDG request (discriminated union) --------------------------------------


class _SDGRequestBase(BaseModel):
    """Shared fields for both SDG modes."""

    model_config = ConfigDict(extra="forbid")

    project_id: UUID = Field(..., description="Owning project.")
    task_type: TaskType
    task_description: str = Field(
        ...,
        min_length=10,
        description="Natural-language description of what the model should learn.",
    )
    num_samples: int = Field(
        ...,
        gt=0,
        le=10_000,
        description="How many synthetic rows to generate.",
    )
    teacher_model: str | None = Field(
        default=None,
        description="Override OPENROUTER_TEACHER_MODEL (e.g. 'openai/gpt-4o-mini').",
    )
    temperature: float = Field(default=0.9, ge=0.0, le=2.0)
    dataset_name: str | None = Field(
        default=None,
        description="Display name for the resulting dataset; defaults to project + timestamp.",
    )


class SDGRequestWithSeed(_SDGRequestBase):
    """Generate by extrapolating from user-provided seed examples."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "sdg_mode": "with_seed",
                    "project_id": "00000000-0000-0000-0000-000000000001",
                    "task_type": "qa",
                    "task_description": "Answer questions about our 30-day return policy",
                    "num_samples": 200,
                    "temperature": 0.9,
                    "seed_data": [
                        {"question": "What's your return window?",
                         "answer": "Items can be returned within 30 days of purchase."},
                        {"question": "Do I need a receipt?",
                         "answer": "Yes — please keep your receipt for any return."},
                        {"question": "Can I get a refund on sale items?",
                         "answer": "Sale items are final sale and cannot be returned."},
                        {"question": "What if my item is damaged?",
                         "answer": "Damaged items can be returned within 60 days for a full refund."},
                        {"question": "Where do I ship returns?",
                         "answer": "Ship returns to our warehouse at 123 Returns Lane, Springfield."},
                    ],
                }
            ]
        },
    )

    sdg_mode: Literal[SDGMode.WITH_SEED] = SDGMode.WITH_SEED
    seed_data: list[dict[str, Any]] = Field(
        ...,
        min_length=5,
        max_length=50,
        description="Seed rows; each must conform to the task_type's data format.",
    )

    @model_validator(mode="after")
    def _seed_rows_match_task_type(self) -> Self:
        # Raises ValidationError pointing at the offending row index.
        parse_samples(self.task_type, self.seed_data)
        return self


class SDGRequestDescriptionOnly(_SDGRequestBase):
    """Generate from a task description alone (no seed examples)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "sdg_mode": "description_only",
                    "project_id": "00000000-0000-0000-0000-000000000002",
                    "task_type": "classification",
                    "task_description": "Classify customer support tickets",
                    "num_samples": 500,
                    "temperature": 0.9,
                    "classification_config": {
                        "labels": ["billing", "technical", "general"]
                    },
                },
                {
                    "sdg_mode": "description_only",
                    "project_id": "00000000-0000-0000-0000-000000000003",
                    "task_type": "tool_calling",
                    "task_description": "Translate kitchen instructions into JSON tool calls",
                    "num_samples": 300,
                    "tool_calling_config": {
                        "tool_definitions": [
                            {
                                "name": "set_oven",
                                "description": "Set oven temperature",
                                "parameters": {
                                    "celsius": {"type": "integer", "required": True}
                                },
                            },
                            {
                                "name": "wait",
                                "description": "Wait for N seconds",
                                "parameters": {
                                    "seconds": {"type": "integer", "required": True}
                                },
                            },
                        ]
                    },
                },
            ]
        },
    )

    sdg_mode: Literal[SDGMode.DESCRIPTION_ONLY] = SDGMode.DESCRIPTION_ONLY
    classification_config: ClassificationGenConfig | None = None
    tool_calling_config: ToolCallingGenConfig | None = None

    @model_validator(mode="after")
    def _task_specific_config_present(self) -> Self:
        if self.task_type is TaskType.CLASSIFICATION and self.classification_config is None:
            raise ValueError(
                "description_only + classification requires classification_config.labels"
            )
        if self.task_type is TaskType.TOOL_CALLING and self.tool_calling_config is None:
            raise ValueError(
                "description_only + tool_calling requires tool_calling_config.tool_definitions"
            )
        # qa needs no extra config.
        # Reject configs that don't match the chosen task.
        if self.task_type is not TaskType.CLASSIFICATION and self.classification_config is not None:
            raise ValueError("classification_config only valid when task_type=classification")
        if self.task_type is not TaskType.TOOL_CALLING and self.tool_calling_config is not None:
            raise ValueError("tool_calling_config only valid when task_type=tool_calling")
        return self


SDGRequest = Annotated[
    SDGRequestWithSeed | SDGRequestDescriptionOnly,
    Field(discriminator="sdg_mode"),
]
"""The full request body for `POST /api/v1/datasets/generate`."""


# --- Responses --------------------------------------------------------------


class SDGJobAcceptedResponse(BaseModel):
    """202 Accepted body — the dataset row exists but has 0 rows until completion."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(..., description="Celery task id; same as the WebSocket channel suffix.")
    dataset_id: UUID = Field(..., description="Placeholder dataset row created at submission.")
    status: JobStatus = Field(default=JobStatus.PENDING)
    websocket_url: str = Field(
        ...,
        description="Convenience URL for the per-job progress stream.",
        examples=["ws://localhost:8000/ws/jobs/celery-task-id-here"],
    )


class SeedUploadResponse(BaseModel):
    """Response for `POST /api/v1/datasets/upload-seed` (multipart upload)."""

    model_config = ConfigDict(extra="forbid")

    dataset_id: UUID
    task_type: TaskType
    num_samples: int
    invalid_rows: list[int] = Field(
        default_factory=list,
        description="Indexes of rows that failed validation (if any were tolerated).",
    )


__all__ = [
    "ClassificationGenConfig",
    "ToolCallingGenConfig",
    "SDGRequestWithSeed",
    "SDGRequestDescriptionOnly",
    "SDGRequest",
    "SDGJobAcceptedResponse",
    "SeedUploadResponse",
]
