"""add datasets.owner_id for per-user ownership that survives orphaning (Supabase sub claim)

Revision ID: 0011_dataset_owner_id
Revises: 0010_dataset_decouple
Create Date: 2026-08-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_dataset_owner_id"
down_revision: str | None = "0010_dataset_decouple"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "datasets",
        sa.Column("owner_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        op.f("ix_datasets_owner_id"),
        "datasets",
        ["owner_id"],
    )

    # Backfill: for datasets still attached to a project, copy the owning
    # Project's owner_id across so ownership is established up front rather
    # than left to be filled in lazily. Datasets that are already orphaned
    # (project_id IS NULL) at the time this migration runs have no project
    # row to copy from and are deliberately left with owner_id NULL -- per
    # the null-fails-closed rule (see Dataset.owner_id doc), that means they
    # become invisible to everyone once auth is required, not "public".
    # There is no other source of truth to backfill them from.
    op.execute(
        sa.text(
            "UPDATE datasets SET owner_id = p.owner_id "
            "FROM projects p WHERE datasets.project_id = p.id"
        )
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_datasets_owner_id"), table_name="datasets")
    op.drop_column("datasets", "owner_id")
