"""TrainingJob ORM model — one fine-tuning run (manual or HPO)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, false
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import Base, TimestampMixin, pg_enum, uuid_pk
from api.schemas.enums import JobStatus, TrainingMode

if TYPE_CHECKING:
    from api.models.dataset import Dataset
    from api.models.model_artifact import ModelArtifact
    from api.models.project import Project


class TrainingJob(Base, TimestampMixin):
    __tablename__ = "training_jobs"

    id: Mapped[UUID] = uuid_pk()
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dataset_id: Mapped[UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    mode: Mapped[TrainingMode] = mapped_column(
        pg_enum(TrainingMode, "training_mode"),
        nullable=False,
    )
    status: Mapped[JobStatus] = mapped_column(
        pg_enum(JobStatus, "job_status"),
        nullable=False,
        default=JobStatus.PENDING,
        index=True,
    )
    celery_task_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        unique=True,
        index=True,
        doc="Used as the public `job_id` and the WebSocket channel suffix.",
    )
    base_model: Mapped[str] = mapped_column(String(256), nullable=False)
    training_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    mlflow_experiment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mlflow_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    config_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        doc="Full ManualTrainingConfig or HPOConfig payload.",
    )

    # HPO-only outcome columns; null for manual runs.
    best_metric_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_params_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    error_message: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # -- auto-pipeline (auto-export + auto-evaluate after training) --------
    auto_export: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=false(),
        doc="If true, kick off GGUF export automatically when training completes.",
    )
    auto_evaluate: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=false(),
        doc="If true, kick off evaluation automatically after export completes.",
    )
    auto_pipeline: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        doc=(
            "Per-stage progress for the auto-export/auto-evaluate pipeline, e.g. "
            '{"export": {"status": "pending|running|completed|failed|skipped", '
            '"artifact_id": str|null, "error": str|null}, '
            '"evaluate": {"status": "pending|running|completed|failed|skipped", '
            '"evaluation_id": str|null, "skip_reason": str|null, "error": str|null}}. '
            "Null until the pipeline is kicked off."
        ),
    )

    project: Mapped["Project"] = relationship(back_populates="training_jobs")
    dataset: Mapped["Dataset"] = relationship(back_populates="training_jobs")
    model_artifact: Mapped["ModelArtifact | None"] = relationship(
        back_populates="training_job",
        uselist=False,
        cascade="all, delete-orphan",
    )
