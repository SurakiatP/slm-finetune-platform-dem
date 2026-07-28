"""Project ORM model — top-level grouping for datasets, trainings, models."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import Base, TimestampMixin, pg_enum, uuid_pk
from api.schemas.enums import TaskType

if TYPE_CHECKING:
    from api.models.dataset import Dataset
    from api.models.training_job import TrainingJob


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    task_type: Mapped[TaskType] = mapped_column(
        pg_enum(TaskType, "task_type"),
        nullable=False,
        index=True,
    )
    external_project_id: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        unique=True,
        index=True,
        doc=(
            "Opaque ID from an external system (e.g. a Supabase project row) "
            "that this Project maps to 1:1. Lets a frontend look up its "
            "backend Project without maintaining its own separate mapping "
            "table (which is otherwise only kept client-side and gets lost "
            "on browser/storage changes)."
        ),
    )

    datasets: Mapped[list["Dataset"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )
    training_jobs: Mapped[list["TrainingJob"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )
