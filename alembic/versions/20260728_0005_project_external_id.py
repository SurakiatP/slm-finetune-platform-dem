"""add projects.external_project_id for durable external system ID mapping

Revision ID: 0005_project_external_id
Revises: 0004_dataset_status
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_project_external_id"
down_revision: str | None = "0004_dataset_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("external_project_id", sa.String(length=200), nullable=True),
    )
    op.create_index(
        op.f("ix_projects_external_project_id"),
        "projects",
        ["external_project_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_projects_external_project_id"), table_name="projects")
    op.drop_column("projects", "external_project_id")
