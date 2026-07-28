"""add datasets.status and datasets.error_message for SDG job tracking

Revision ID: 0004_dataset_status
Revises: 0003_dataset_parent_id
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_dataset_status"
down_revision: str | None = "0003_dataset_parent_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The `job_status` Postgres enum type was already created by 0001_initial for
# training_jobs.status / evaluation_runs.status. Reference it here without
# re-creating it.
_JOB_STATUS_ENUM = sa.Enum(
    "pending", "running", "completed", "failed", "cancelled",
    name="job_status",
    create_type=False,
)


def upgrade() -> None:
    op.add_column(
        "datasets",
        sa.Column(
            "status",
            _JOB_STATUS_ENUM,
            nullable=False,
            server_default="completed",
        ),
    )
    op.add_column(
        "datasets",
        sa.Column("error_message", sa.String(length=4000), nullable=True),
    )
    op.create_index(op.f("ix_datasets_status"), "datasets", ["status"])


def downgrade() -> None:
    op.drop_index(op.f("ix_datasets_status"), table_name="datasets")
    op.drop_column("datasets", "error_message")
    op.drop_column("datasets", "status")
