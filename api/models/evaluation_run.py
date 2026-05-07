"""EvaluationRun ORM model — metrics for one (model, dataset) pair."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import DateTime, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import Base, TimestampMixin, pg_enum, uuid_pk
from api.schemas.enums import JobStatus

if TYPE_CHECKING:
    from api.models.dataset import Dataset
    from api.models.model_artifact import ModelArtifact


class EvaluationRun(Base, TimestampMixin):
    __tablename__ = "evaluation_runs"

    id: Mapped[UUID] = uuid_pk()
    model_artifact_id: Mapped[UUID] = mapped_column(
        ForeignKey("model_artifacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dataset_id: Mapped[UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    celery_task_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        unique=True,
        index=True,
    )
    status: Mapped[JobStatus] = mapped_column(
        pg_enum(JobStatus, "job_status"),
        nullable=False,
        default=JobStatus.PENDING,
    )

    # Per-task metrics keyed by metric name (accuracy / f1 / rougeL / json_validity / ...).
    metrics_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Optional LLM-as-judge score (mean across rows).
    llm_judge_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_judge_model: Mapped[str | None] = mapped_column(String(256), nullable=True)

    error_message: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    model_artifact: Mapped["ModelArtifact"] = relationship(back_populates="evaluation_runs")
    dataset: Mapped["Dataset"] = relationship(back_populates="evaluation_runs")
