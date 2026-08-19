"""Dataset ORM model — pointer to a row collection in MinIO."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import Base, TimestampMixin, pg_enum, uuid_pk
from api.schemas.enums import DatasetSource, JobStatus, TaskType

if TYPE_CHECKING:
    from api.models.evaluation_run import EvaluationRun
    from api.models.project import Project
    from api.models.training_job import TrainingJob


class Dataset(Base, TimestampMixin):
    __tablename__ = "datasets"

    id: Mapped[UUID] = uuid_pk()
    # project_id is nullable so a dataset can outlive its project: deleting a
    # Project now sets this to NULL (ondelete="SET NULL") instead of
    # cascading the delete into the dataset. This decouples dataset
    # lifecycle from project lifecycle (see W1-T1).
    #
    # NOTE: who "owns" an orphaned dataset (project_id IS NULL) is answered
    # by owner_id below -- it is copied from the owning Project's owner_id
    # at creation time and, unlike project_id, does NOT get cleared when the
    # project is deleted, so orphaned datasets stay visible to their owner.
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    owner_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
        doc=(
            "Supabase auth 'sub' claim (a UUID string) identifying the user "
            "who owns this Dataset. Not a foreign key to any local table -- "
            "there is no local users table and there will not be one; "
            "identity comes entirely from Supabase (mirrors "
            "Project.owner_id). "
            "Copied from the owning Project's owner_id at creation time and, "
            "critically, SURVIVES the dataset being orphaned (project_id set "
            "to NULL on project delete) -- that persistence is this "
            "column's whole purpose: without it, an orphaned dataset would "
            "have no way to know who it still belongs to. "
            "Nullable for rows created before this column existed, or "
            "created from a project that itself had no owner_id yet. "
            "Enforcement rule for whoever reads this column later: once "
            "authentication is required, rows with owner_id IS NULL are "
            "visible to NOBODY -- this fails closed, not open. Do not treat "
            "null as 'public'."
        ),
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    task_type: Mapped[TaskType] = mapped_column(
        pg_enum(TaskType, "task_type"),
        nullable=False,
        index=True,
    )
    source: Mapped[DatasetSource] = mapped_column(
        pg_enum(DatasetSource, "dataset_source"),
        nullable=False,
    )
    status: Mapped[JobStatus] = mapped_column(
        pg_enum(JobStatus, "job_status"),
        nullable=False,
        default=JobStatus.PENDING,
        index=True,
        doc=(
            "Lifecycle of dataset population. Seed uploads are COMPLETED "
            "immediately (synchronous). SDG-generated datasets start PENDING "
            "and are flipped to RUNNING/COMPLETED/FAILED by the Celery worker."
        ),
    )
    error_message: Mapped[str | None] = mapped_column(
        String(4000),
        nullable=True,
        doc="Populated when status=FAILED (SDG worker exception message, truncated to 4000 chars).",
    )
    num_samples: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    storage_uri: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        doc="s3://{bucket}/{key} pointing into MinIO. Null until generation completes.",
    )
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    generation_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        doc="SDG context (teacher_model, sdg_mode, num_invalid_rows, ...) when source=sdg.",
    )
    parent_dataset_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        doc=(
            "If set, this is a holdout child of parent_dataset_id (SDG over-"
            "generation). `generation_metadata.role` carries 'train'|'holdout'."
        ),
    )
    celery_task_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
        doc=(
            "Used as the public `job_id` and the WebSocket channel suffix for "
            "SDG-generated datasets. Unlike TrainingJob.celery_task_id this is "
            "NOT unique: it is backfilled from pre-existing "
            "generation_metadata['celery_task_id'] JSONB values, whose "
            "uniqueness cannot be guaranteed, so do not add a unique "
            "constraint here."
        ),
    )

    project: Mapped["Project"] = relationship(back_populates="datasets")
    training_jobs: Mapped[list["TrainingJob"]] = relationship(back_populates="dataset")
    evaluation_runs: Mapped[list["EvaluationRun"]] = relationship(back_populates="dataset")
    parent: Mapped["Dataset | None"] = relationship(
        "Dataset",
        remote_side="Dataset.id",
        back_populates="holdout_children",
    )
    holdout_children: Mapped[list["Dataset"]] = relationship(
        "Dataset",
        back_populates="parent",
        cascade="all, delete-orphan",
    )
