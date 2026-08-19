"""dataset.project_id nullable (SET NULL on project delete) + training_jobs auto-pipeline columns

Revision ID: 0010_dataset_decouple
Revises: 0009_usage_events
Create Date: 2026-08-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_dataset_decouple"
down_revision: str | None = "0009_usage_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- datasets.project_id: decouple dataset lifecycle from project ------
    # Was NOT NULL + ondelete=CASCADE (deleting a project deleted its
    # datasets). Now nullable + ondelete=SET NULL so a dataset survives its
    # project being deleted. The FK constraint must be dropped and
    # recreated since ondelete is part of the constraint definition, not
    # something ALTER COLUMN can change on its own.
    op.drop_constraint(
        op.f("fk_datasets_project_id_projects"), "datasets", type_="foreignkey"
    )
    op.alter_column(
        "datasets",
        "project_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    op.create_foreign_key(
        op.f("fk_datasets_project_id_projects"),
        "datasets",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # -- training_jobs auto-pipeline columns --------------------------------
    op.add_column(
        "training_jobs",
        sa.Column(
            "auto_export",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "training_jobs",
        sa.Column(
            "auto_evaluate",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "training_jobs",
        sa.Column(
            "auto_pipeline",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("training_jobs", "auto_pipeline")
    op.drop_column("training_jobs", "auto_evaluate")
    op.drop_column("training_jobs", "auto_export")

    # Restore datasets.project_id to NOT NULL + ondelete=CASCADE. Any rows
    # left with project_id IS NULL by the time this runs (orphaned datasets
    # created while this migration was applied) will fail the NOT NULL
    # alter -- that's expected: those rows have no project to reattach to,
    # and this downgrade doesn't attempt automatic reassignment/deletion.
    op.drop_constraint(
        op.f("fk_datasets_project_id_projects"), "datasets", type_="foreignkey"
    )
    op.alter_column(
        "datasets",
        "project_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.create_foreign_key(
        op.f("fk_datasets_project_id_projects"),
        "datasets",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )
