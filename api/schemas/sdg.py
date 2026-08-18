"""Synthetic Data Generation (SDG) request / response schemas.

`POST /api/v1/datasets/generate` accepts an `SDGRequest` and returns an
`SDGJobAcceptedResponse`. The actual generation runs as a Celery task and
publishes progress to the WebSocket channel `job:{job_id}`
(see schemas/progress.py).

Phase 9 changes:
  • `SDGRequestWithSeed.seed_data` (inline list[dict]) is replaced with
    `seed_dataset_id: UUID` — references a previously-uploaded seed
    dataset (POST /api/v1/datasets/upload-seed).
  • `teacher_model` field removed — Phase 9 model strings are hardcoded
    in `ai_engine/data_gen/models.py` (Q6.1).
"""

from __future__ import annotations

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from api.schemas.data_formats import ToolDefinition
from api.schemas.enums import JobStatus, SDGMode, TaskType
from api.schemas.upload import FormatDetectionReport

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
    holdout_size: int = Field(
        default=100,
        ge=0,
        le=2_000,
        description=(
            "Extra rows generated beyond `num_samples`, persisted as a separate "
            "child Dataset (linked via parent_dataset_id) for hold-out evaluation. "
            "Set to 0 to disable. Stratified by label (classification) / tool "
            "name (tool_calling); random for QA."
        ),
    )
    temperature: float = Field(default=0.9, ge=0.0, le=2.0)
    dataset_name: str | None = Field(
        default=None,
        description="Display name for the resulting dataset; defaults to project + timestamp.",
    )
    holdout_name: str | None = Field(
        default=None,
        description="Display name for the holdout dataset; defaults to '<train dataset name>-holdout'.",
    )


class SDGRequestWithSeed(_SDGRequestBase):
    """Generate by extrapolating from a previously-uploaded seed dataset.

    Upload the seed first via `POST /api/v1/datasets/upload-seed`, then
    pass the returned `dataset_id` here as `seed_dataset_id`.
    """

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
                    "holdout_size": 50,
                    "temperature": 0.9,
                    "seed_dataset_id": "00000000-0000-0000-0000-000000000099",
                }
            ]
        },
    )

    sdg_mode: Literal[SDGMode.WITH_SEED] = SDGMode.WITH_SEED
    seed_dataset_id: UUID = Field(
        ...,
        description=(
            "ID of a previously-uploaded seed dataset "
            "(POST /api/v1/datasets/upload-seed). The dataset's source must "
            "be DatasetSource.SEED and task_type must match this request's "
            "task_type. For QA + PDF, the dataset's metadata must contain "
            "a `pdf_uri`."
        ),
    )


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
                    "holdout_size": 100,
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
                    "holdout_size": 100,
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
    """Response for `POST /api/v1/datasets/upload-seed` (multipart upload).

    Phase 9: extended with `format_detection` (the audit trail of the
    schema-mapping pass) and `pdf_uri` (set only for QA + PDF uploads).
    """

    model_config = ConfigDict(extra="forbid")

    dataset_id: UUID
    task_type: TaskType
    num_samples: int = Field(
        ...,
        ge=0,
        description=(
            "Number of canonicalised rows persisted to MinIO. 0 for PDF "
            "uploads — Q&A pairs come from the SDG generator later."
        ),
    )
    invalid_rows: list[int] = Field(
        default_factory=list,
        description="Indexes of rows that failed validation (if any were tolerated).",
    )
    format_detection: FormatDetectionReport = Field(
        ...,
        description=(
            "Audit trail of the Format Detection pass. `ran=False` when the "
            "seed was already canonical or for PDF uploads."
        ),
    )
    pdf_uri: str | None = Field(
        default=None,
        description=(
            "S3 URI of the persisted PDF (only set for QA + PDF uploads)."
        ),
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
