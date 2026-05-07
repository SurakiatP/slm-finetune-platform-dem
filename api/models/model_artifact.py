"""ModelArtifact ORM model — output weights of a successful TrainingJob."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import Base, TimestampMixin, uuid_pk

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

    training_job: Mapped["TrainingJob"] = relationship(back_populates="model_artifact")
    evaluation_runs: Mapped[list["EvaluationRun"]] = relationship(back_populates="model_artifact")
