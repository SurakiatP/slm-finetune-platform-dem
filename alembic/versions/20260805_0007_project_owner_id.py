"""add projects.owner_id for per-user ownership (Supabase sub claim)

Revision ID: 0007_project_owner_id
Revises: 0006_job_control_columns
Create Date: 2026-08-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_project_owner_id"
down_revision: str | None = "0006_job_control_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("owner_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        op.f("ix_projects_owner_id"),
        "projects",
        ["owner_id"],
    )

    # No backfill. There is no source of truth to backfill owner_id *from* —
    # pre-existing rows were created before per-user ownership existed, and
    # there is nothing in this database that says who "should" own them.
    # Assigning existing rows an owner is a deliberate operational step to be
    # taken (by someone, by hand or by a separate one-off script informed by
    # real data) before authentication is switched from optional to required —
    # not something this migration should guess at.


def downgrade() -> None:
    op.drop_index(op.f("ix_projects_owner_id"), table_name="projects")
    op.drop_column("projects", "owner_id")
