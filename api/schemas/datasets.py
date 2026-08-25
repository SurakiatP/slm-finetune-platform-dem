"""Dataset response / preview schemas (request side lives in `sdg.py`)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from api.schemas.enums import DatasetSource, JobStatus, TaskType


class DatasetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    project_id: UUID | None = Field(
        default=None,
        description=(
            "Owning project. Null for an orphaned dataset — one whose "
            "project was deleted (ondelete=SET NULL, see W1-T1) while the "
            "dataset itself survives."
        ),
    )
    parent_dataset_id: UUID | None = Field(
        default=None,
        description=(
            "If set, this dataset is a holdout child of another dataset (created "
            "by SDG over-generation). Use the parent for training and this one "
            "for `POST /evaluations` to get a leak-free judge score."
        ),
    )
    seed_dataset_id: UUID | None = Field(
        default=None,
        description=(
            "For with_seed SDG generation, the seed dataset this one was "
            "bootstrapped from. Null for description_only generations and for "
            "datasets that are not themselves SDG output. Also mirrored at "
            "generation_metadata.seed_dataset_id for backward compatibility."
        ),
    )
    name: str
    task_type: TaskType
    source: DatasetSource
    status: JobStatus = Field(
        ...,
        description=(
            "Lifecycle of dataset population: pending (SDG queued) / running "
            "(SDG worker executing) / completed (rows persisted, ready to use) / "
            "failed (see error_message) / cancelled."
        ),
    )
    error_message: str | None = Field(
        default=None,
        description="Populated when status=failed — the SDG worker's exception message.",
    )
    num_samples: int
    storage_uri: str | None
    size_bytes: int | None
    generation_metadata: dict[str, Any] | None
    celery_task_id: str | None = Field(
        default=None,
        description=(
            "Celery task id for SDG-generated datasets — the WebSocket job id "
            "used to reconnect to `/ws/jobs/{id}` and `GET /jobs/{id}/progress` "
            "after a page reload. Also mirrored at "
            "generation_metadata.celery_task_id for backward compatibility."
        ),
    )
    created_at: datetime
    updated_at: datetime
    owner_id: str | None = Field(
        default=None,
        description=(
            "Supabase sub of the owner, copied from the owning project at "
            "creation. Survives project deletion (the dataset is orphaned, "
            "not the ownership record). Null means unowned — invisible "
            "under auth."
        ),
    )


class DatasetUpdate(BaseModel):
    """Body of `PATCH /datasets/{id}` — rename only.

    `extra="forbid"` so a caller trying to smuggle another field through
    (e.g. `project_id`, to re-parent a dataset) gets a 422 instead of that
    field being silently ignored — this endpoint does exactly one thing.
    `max_length` mirrors `Dataset.name`'s `String(200)` column (see
    `api/models/dataset.py`) so an over-long name 422s here rather than
    surfacing as a DB error later.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=200)


class DatasetPreviewResponse(BaseModel):
    """Body of `GET /datasets/{id}/preview?limit=N`."""

    model_config = ConfigDict(extra="forbid")

    dataset_id: UUID
    task_type: TaskType
    samples: list[dict[str, Any]] = Field(
        ...,
        description="First N rows; row shape matches data_formats.<TaskType>Sample.",
    )
    total: int


class JudgeDimensionStats(BaseModel):
    """Aggregate LLM-judge score for a single scored dimension."""

    model_config = ConfigDict(extra="forbid")

    mean: float
    histogram: list[int]


class JudgeStats(BaseModel):
    """LLM-judge aggregates for a dataset (or a subset keyed by `judge_by_key`)."""

    model_config = ConfigDict(extra="forbid")

    count: int
    fidelity: JudgeDimensionStats
    naturalness: JudgeDimensionStats
    utility: JudgeDimensionStats
    weighted: JudgeDimensionStats


class InsightLabelCount(BaseModel):
    """One label's share of the scanned rows."""

    model_config = ConfigDict(extra="forbid")

    label: str
    count: int
    percent: float


class InsightLengthBucket(BaseModel):
    """One bucket of the sample-length histogram."""

    model_config = ConfigDict(extra="forbid")

    bucket: str
    count: int


class InsightIssue(BaseModel):
    """A single flagged data-quality issue surfaced by the insights scan."""

    model_config = ConfigDict(extra="forbid")

    id: str
    severity: Literal["critical", "warning", "info"]
    category: str
    title: str
    description: str
    affected_rows: int
    suggestion: str


class GenerationCounts(BaseModel):
    """SDG generation funnel counts, when available.

    Null for datasets that carry no stored SDG generation metadata (e.g.
    uploaded/legacy datasets) — every field is independently optional
    because older generation_metadata payloads may not have recorded all
    of them.
    """

    model_config = ConfigDict(extra="forbid")

    generated: int | None = None
    target: int | None = None
    schema_rejected: int | None = None
    duplicates_removed: int | None = None
    # Rows dropped by the embedding-based semantic pass that ran after MinHash; None for datasets generated before the feature or with it disabled.
    semantic_duplicates_removed: int | None = None
    judge_rejected: int | None = None
    judge_parse_failures: int | None = None


class DatasetInsightsResponse(BaseModel):
    """Body of `GET /datasets/{id}/insights` — data-quality scan results."""

    model_config = ConfigDict(extra="forbid")

    dataset_id: UUID
    task_type: TaskType
    row_count: int
    scanned_rows: int
    scan_truncated: bool
    label_distribution: list[InsightLabelCount]
    near_duplicate_count: int
    duplicate_rows: int
    missing_labels: int
    outliers: int
    length_distribution: list[InsightLengthBucket]
    issues: list[InsightIssue]
    overall_quality_score: int = Field(..., ge=0, le=100)
    readiness: Literal["ready", "caveats", "fix"]
    judge: JudgeStats | None = Field(
        default=None,
        description=(
            "LLM-judge aggregates for this dataset. Null for uploaded/legacy "
            "datasets that carry no stored SDG aggregates."
        ),
    )
    judge_by_key: dict[str, JudgeStats] | None = None
    counts: GenerationCounts | None = None


__all__ = [
    "DatasetResponse",
    "DatasetUpdate",
    "DatasetPreviewResponse",
    "JudgeDimensionStats",
    "JudgeStats",
    "InsightLabelCount",
    "InsightLengthBucket",
    "InsightIssue",
    "GenerationCounts",
    "DatasetInsightsResponse",
]
