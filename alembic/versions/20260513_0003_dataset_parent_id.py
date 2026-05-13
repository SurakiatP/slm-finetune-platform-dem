"""add datasets.parent_dataset_id for SDG holdout child datasets

Revision ID: 0003_dataset_parent_id
Revises: 0001_initial
Create Date: 2026-05-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_dataset_parent_id"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "datasets",
        sa.Column("parent_dataset_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_datasets_parent_dataset_id_datasets",
        source_table="datasets",
        referent_table="datasets",
        local_cols=["parent_dataset_id"],
        remote_cols=["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_datasets_parent_dataset_id",
        "datasets",
        ["parent_dataset_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_datasets_parent_dataset_id", table_name="datasets")
    op.drop_constraint(
        "fk_datasets_parent_dataset_id_datasets",
        "datasets",
        type_="foreignkey",
    )
    op.drop_column("datasets", "parent_dataset_id")
