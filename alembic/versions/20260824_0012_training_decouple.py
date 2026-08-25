"""training_jobs.project_id nullable (SET NULL on project delete)

User decision (D10): deleting a project must KEEP its trained models.
`TrainingJob.project_id` was NOT NULL + ondelete=CASCADE (deleting a project
deleted all of its training runs, which cascaded on to delete their
`ModelArtifact` rows via `TrainingJob.model_artifact`'s own
`cascade="all, delete-orphan"`). Now nullable + ondelete=SET NULL so a
training run — and the model it produced — survives its project being
deleted. Mirrors `20260818_0010_dataset_decouple.py`'s treatment of
`datasets.project_id` exactly: drop the FK (ondelete is part of the
constraint definition, not something ALTER COLUMN can change on its own),
relax the column, then recreate the FK with SET NULL.

Unlike datasets (migration 0011), training_jobs does NOT get its own
`owner_id` column this round — see `api/services/ownership.py`'s module
docstring for how orphaned training/model/evaluation rows are scoped
without one (fail-closed: invisible to authenticated callers, fully
visible when auth is off).

Revision ID: 0012_training_decouple
Revises: 0011_dataset_owner_id
Create Date: 2026-08-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_training_decouple"
down_revision: str | None = "0011_dataset_owner_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("fk_training_jobs_project_id_projects"),
        "training_jobs",
        type_="foreignkey",
    )
    op.alter_column(
        "training_jobs",
        "project_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    op.create_foreign_key(
        op.f("fk_training_jobs_project_id_projects"),
        "training_jobs",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    # Restore training_jobs.project_id to NOT NULL + ondelete=CASCADE. Any
    # rows left with project_id IS NULL by the time this runs (orphaned
    # training runs created while this migration was applied — e.g. a
    # project deleted after this migration shipped) will fail the NOT NULL
    # alter -- that's expected: those rows have no project to reattach to,
    # and this downgrade doesn't attempt automatic reassignment/deletion.
    op.drop_constraint(
        op.f("fk_training_jobs_project_id_projects"),
        "training_jobs",
        type_="foreignkey",
    )
    op.alter_column(
        "training_jobs",
        "project_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.create_foreign_key(
        op.f("fk_training_jobs_project_id_projects"),
        "training_jobs",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )
