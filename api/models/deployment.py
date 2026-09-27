"""Deployment ORM model — a logical serving slot on the shared Ollama host.

There is no separate "active"/"stopped" boolean: this reuses the shared
`job_status` Postgres enum (see `api/models/base.py::pg_enum`) exactly like
Dataset/TrainingJob/EvaluationRun do. For a Deployment, RUNNING means the
model is actively preloaded (keep_alive=-1) and COMPLETED means it has been
cleanly stopped (keep_alive=0) — "completed" here is a stopped/terminal
deployment, not a failure state.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import Base, TimestampMixin, pg_enum, uuid_pk
from api.schemas.enums import JobStatus


class Deployment(Base, TimestampMixin):
    __tablename__ = "deployments"

    id: Mapped[UUID] = uuid_pk()
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # No relationship declared on ModelArtifact (see plan T1) — deployments
    # are looked up from the artifact side via an explicit query, not an ORM
    # relationship, to avoid coupling model_artifact.py to this module.
    model_artifact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("model_artifacts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        pg_enum(JobStatus, "job_status"),
        nullable=False,
        default=JobStatus.PENDING,
        index=True,
    )
    rate_limit_per_min: Mapped[int] = mapped_column(Integer, nullable=False)
    celery_task_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
        doc="Used as the public `job_id` for the preload task.",
    )
    error_message: Mapped[str | None] = mapped_column(String(4000), nullable=True)
