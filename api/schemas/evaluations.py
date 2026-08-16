"""Evaluation request / response / compare schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from api.schemas.enums import JobStatus


class EvaluationCreate(BaseModel):
    """Body for `POST /api/v1/evaluations`."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "model_artifact_id": "00000000-0000-0000-0000-000000000020",
                    "dataset_id": "00000000-0000-0000-0000-000000000010",
                    "use_llm_judge": True,
                    "judge_model": "anthropic/claude-3.5-sonnet",
                }
            ]
        },
    )

    model_artifact_id: UUID
    dataset_id: UUID
    use_llm_judge: bool = False
    judge_model: str | None = Field(
        default=None,
        description="Override LLM_JUDGE_MODEL when use_llm_judge=True.",
    )


class EvaluationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    model_artifact_id: UUID
    dataset_id: UUID
    celery_task_id: str | None
    status: JobStatus
    metrics_json: dict[str, Any] | None
    llm_judge_score: float | None
    llm_judge_model: str | None
    error_message: str | None
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime
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


class EvaluationCompareRequest(BaseModel):
    """Body for `POST /api/v1/evaluations/compare`."""

    model_config = ConfigDict(extra="forbid")

    evaluation_ids: list[UUID] = Field(..., min_length=2, max_length=10)


class EvaluationCompareResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_ids: list[UUID]
    # metric_name → {evaluation_id → value}
    metrics: dict[str, dict[str, float | None]]
    judge_scores: dict[str, float | None]


class EvaluationAcceptedResponse(BaseModel):
    """202 body for newly-enqueued evaluation jobs."""

    model_config = ConfigDict(extra="forbid")

    evaluation_id: UUID
    job_id: str
    status: JobStatus
    websocket_url: str


__all__ = [
    "EvaluationCreate",
    "EvaluationResponse",
    "EvaluationCompareRequest",
    "EvaluationCompareResponse",
    "EvaluationAcceptedResponse",
]
