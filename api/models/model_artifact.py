"""ModelArtifact ORM model — output weights of a successful TrainingJob."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import Base, TimestampMixin, pg_enum, uuid_pk
from api.schemas.enums import JobStatus

if TYPE_CHECKING:
    from api.models.evaluation_run import EvaluationRun
    from api.models.training_job import TrainingJob


class ModelArtifact(Base, TimestampMixin):
    __tablename__ = "model_artifacts"

    id: Mapped[UUID] = uuid_pk()
    training_job_id: Mapped[UUID] = mapped_column(
        ForeignKey("training_jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_model: Mapped[str] = mapped_column(String(256), nullable=False)
    mlflow_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # Storage URIs are populated lazily as exports happen.
    lora_adapter_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    gguf_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    safetensors_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Convenience: sum of artifact sizes in MB.
    size_mb: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Tag used by Ollama (`model:tag`) once the GGUF has been registered.
    ollama_model_tag: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True)

    # Last export failure (B7). Cleared on successful re-export. Null = either
    # no export attempted yet, currently running, or last attempt succeeded —
    # callers disambiguate by checking gguf_uri / safetensors_uri.
    export_error_message: Mapped[str | None] = mapped_column(String(4000), nullable=True)

    # Job-control columns for the export pipeline. These are additive — the
    # existing completion signal (gguf_uri / safetensors_uri populated, or
    # export_error_message set) remains the contract callers rely on today.
    # export_status/export_celery_task_id exist purely so an in-flight or
    # queued export can be recovered/cancelled by job id after a reload.
    export_status: Mapped[JobStatus | None] = mapped_column(
        pg_enum(JobStatus, "job_status"),
        nullable=True,
        doc=(
            "Lifecycle of the most recent export request (pending/running/"
            "completed/failed/cancelled). Null means no export was ever "
            "requested for this artifact. Reuses the shared `job_status` "
            "Postgres enum type used by Dataset/TrainingJob/EvaluationRun."
        ),
    )
    export_celery_task_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
        doc=(
            "Celery task id of the most recent export.apply_async() call — "
            "the public `job_id` and WebSocket channel suffix for the export "
            "job. Null until the first export is submitted."
        ),
    )

    training_job: Mapped["TrainingJob"] = relationship(back_populates="model_artifact")
    evaluation_runs: Mapped[list["EvaluationRun"]] = relationship(back_populates="model_artifact")
