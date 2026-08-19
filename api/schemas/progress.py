"""WebSocket message schemas streamed from `/ws/jobs/{job_id}`.

Workers publish JSON messages to the Redis channel `job:{job_id}`. The WebSocket
endpoint forwards them to clients verbatim, so these schemas are also the
exact wire format.

All messages share `type` (the discriminator) and `job_id`. Per-message fields
are defined below.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from api.schemas.enums import WSMessageType

# --- Base -------------------------------------------------------------------


class _WSMessageBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(..., description="Celery task id; matches the channel suffix.")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC time the worker emitted this message.",
    )


# --- Per-message-type payloads ---------------------------------------------


class SDGProgress(_WSMessageBase):
    type: Literal[WSMessageType.SDG_PROGRESS] = WSMessageType.SDG_PROGRESS
    phase: Literal[
        # Phase 4 phases (kept for back-compat with existing handlers).
        "generating",
        "validating",
        "deduplicating",
        "persisting",
        # Phase 9 phases — published by the new orchestrator.
        "format_detection",
        "meta_prompting",
        "judging",
        "dedup",
        # End-game phases — published by the worker (`workers/tasks/
        # data_generation.py`) after the generation loop finishes, around
        # the deterministic train/hold-out split and the two persist steps.
        "splitting_holdout",
        "persisting_train",
        "persisting_holdout",
    ] = "generating"
    samples_generated: int = Field(default=0, ge=0)
    samples_target: int = Field(..., ge=1)
    samples_valid: int = Field(default=0, ge=0)
    samples_rejected: int = Field(default=0, ge=0)
    duplicates_removed: int = Field(default=0, ge=0)
    # ---- Phase 9 additions (all optional so old emitters still validate) -
    current_loop: int | None = Field(
        default=None,
        ge=0,
        description="0-indexed SDG loop iteration; None outside the loop body.",
    )
    judge_rejected: int | None = Field(
        default=None,
        ge=0,
        description="Rows discarded because the LLM judge scored them below threshold.",
    )
    judge_parse_failures: int | None = Field(
        default=None,
        ge=0,
        description="Judge responses that did not parse into the JudgeScore schema.",
    )
    dedup_rejected: int | None = Field(
        default=None,
        ge=0,
        description="Rows discarded by the MinHash LSH near-duplicate filter.",
    )


class TrainingProgress(_WSMessageBase):
    type: Literal[WSMessageType.TRAINING_PROGRESS] = WSMessageType.TRAINING_PROGRESS
    epoch: float = Field(..., ge=0.0, description="Fractional epoch (e.g. 1.5).")
    epochs_total: int = Field(..., ge=1)
    step: int = Field(..., ge=0)
    steps_total: int = Field(..., ge=1)
    train_loss: float | None = None
    eval_loss: float | None = None
    learning_rate: float | None = None
    samples_per_second: float | None = None
    gpu_memory_mb: float | None = Field(default=None, ge=0.0)


class HPOProgress(_WSMessageBase):
    type: Literal[WSMessageType.HPO_PROGRESS] = WSMessageType.HPO_PROGRESS
    trial_number: int = Field(..., ge=0, description="0-indexed Optuna trial number.")
    trials_total: int = Field(..., ge=1)
    current_params: dict[str, str | int | float | bool] | None = None
    best_value: float | None = None
    best_params: dict[str, str | int | float | bool] | None = None
    last_trial_value: float | None = None
    last_trial_pruned: bool = False
    # Optional nested per-step training progress for the current trial.
    inner_progress: TrainingProgress | None = None


class ExportProgress(_WSMessageBase):
    type: Literal[WSMessageType.EXPORT_PROGRESS] = WSMessageType.EXPORT_PROGRESS
    stage: Literal[
        "downloading",
        "merging",
        "converting",
        "quantizing",
        "uploading",
        "registering",
    ] = Field(..., description="Current step of the GGUF/SafeTensors export pipeline.")
    detail: str | None = Field(
        default=None,
        description="Free-text sub-status (e.g. the quantization level); for display only.",
    )


class EvaluationProgress(_WSMessageBase):
    type: Literal[WSMessageType.EVALUATION_PROGRESS] = WSMessageType.EVALUATION_PROGRESS
    phase: Literal["predicting", "scoring", "judging"] = Field(
        ..., description="Current step of the evaluation pipeline."
    )
    rows_done: int = Field(..., ge=0, description="Rows processed so far in the current phase.")
    rows_total: int = Field(..., ge=0, description="Total rows to process in the current phase.")


class JobCompleted(_WSMessageBase):
    type: Literal[WSMessageType.COMPLETED] = WSMessageType.COMPLETED
    # Task-shaped result payload — kept loose because SDG / training / eval differ.
    result: dict[str, Any] = Field(default_factory=dict)
    mlflow_run_id: str | None = None
    dataset_id: UUID | None = None
    model_artifact_id: UUID | None = None


class JobFailed(_WSMessageBase):
    type: Literal[WSMessageType.FAILED] = WSMessageType.FAILED
    error: str = Field(..., min_length=1, description="Human-readable error message.")
    error_type: str | None = Field(
        default=None,
        description="Exception class name (e.g. 'OutOfMemoryError').",
    )
    traceback: str | None = Field(
        default=None,
        description="Full traceback; emit only when LOG_LEVEL=DEBUG.",
    )


WSMessage = Annotated[
    SDGProgress
    | TrainingProgress
    | HPOProgress
    | ExportProgress
    | EvaluationProgress
    | JobCompleted
    | JobFailed,
    Field(discriminator="type"),
]
"""Anything published to `job:{job_id}` must validate against this union."""


__all__ = [
    "SDGProgress",
    "TrainingProgress",
    "HPOProgress",
    "ExportProgress",
    "EvaluationProgress",
    "JobCompleted",
    "JobFailed",
    "WSMessage",
]
