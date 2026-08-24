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
    owner_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
        doc=(
            "Supabase auth 'sub' claim (a UUID string) identifying the user "
            "who owns this Project. Not a foreign key to any local table — "
            "there is no local users table and there will not be one; "
            "identity comes entirely from Supabase. "
            "Nullable deliberately, for two reasons: (1) rows created before "
            "this column existed have no owner, and (2) this branch ships in "
            "a phase-1 compatibility mode where requests may legitimately "
            "arrive with no authenticated user at all. "
            "Enforcement rule for whoever reads this column later: once "
            "authentication is required, rows with owner_id IS NULL are "
            "visible to NOBODY — this fails closed, not open. Do not treat "
            "null as 'public'."
        ),
    )

    datasets: Mapped[list["Dataset"]] = relationship(
        back_populates="project",
        # No delete/delete-orphan cascade: datasets must SURVIVE project
        # deletion as orphans (project_id -> NULL). The DB enforces this via
        # the FK's ondelete="SET NULL" (migration 0010); passive_deletes="all"
        # stops the ORM unit-of-work from pre-empting it by loading children
        # and either deleting them or nulling the FK itself.
        cascade="save-update, merge",
        passive_deletes="all",
    )
    training_jobs: Mapped[list["TrainingJob"]] = relationship(
        back_populates="project",
        # No delete/delete-orphan cascade: training runs (and their
        # ModelArtifacts) must SURVIVE project deletion as orphans
        # (project_id -> NULL), per user decision D10. The DB enforces this
        # via the FK's ondelete="SET NULL" (migration 0012_training_decouple);
        # passive_deletes="all" stops the ORM unit-of-work from pre-empting
        # it by loading children and either deleting them or nulling the FK
        # itself. Mirrors `datasets` above exactly (migration 0010).
        cascade="save-update, merge",
        passive_deletes="all",
    )
