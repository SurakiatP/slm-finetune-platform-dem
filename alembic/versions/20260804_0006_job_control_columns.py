"""add job-control columns: datasets.celery_task_id, model_artifacts.export_status/export_celery_task_id

Revision ID: 0006_job_control_columns
Revises: 0005_project_external_id
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_job_control_columns"
down_revision: str | None = "0005_project_external_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The `job_status` Postgres enum type was already created by 0001_initial for
# training_jobs.status / evaluation_runs.status (and reused by 0004 for
# datasets.status). Reference it here without re-creating it.
_JOB_STATUS_ENUM = sa.Enum(
    "pending", "running", "completed", "failed", "cancelled",
    name="job_status",
    create_type=False,
)


def upgrade() -> None:
    # -- datasets.celery_task_id ------------------------------------------
    op.add_column(
        "datasets",
        sa.Column("celery_task_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        op.f("ix_datasets_celery_task_id"), "datasets", ["celery_task_id"]
    )

    # Backfill from the existing generation_metadata JSONB blob for rows
    # generated before this column existed. Use the ->> text-extraction
    # operator with an "IS NOT NULL" guard instead of the `?` (jsonb_exists)
    # operator, since `?` collides with SQLAlchemy's text() bind-parameter
    # placeholder syntax and would need escaping (`??`) to survive — the
    # ->> form below needs no escaping and is unambiguous.
    op.execute(
        """
        UPDATE datasets
        SET celery_task_id = generation_metadata ->> 'celery_task_id'
        WHERE generation_metadata ->> 'celery_task_id' IS NOT NULL
        """
    )

    # -- model_artifacts.export_status / export_celery_task_id ------------
    op.add_column(
        "model_artifacts",
        sa.Column("export_status", _JOB_STATUS_ENUM, nullable=True),
    )
    op.add_column(
        "model_artifacts",
        sa.Column("export_celery_task_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        op.f("ix_model_artifacts_export_celery_task_id"),
        "model_artifacts",
        ["export_celery_task_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_model_artifacts_export_celery_task_id"),
        table_name="model_artifacts",
    )
    op.drop_column("model_artifacts", "export_celery_task_id")
    op.drop_column("model_artifacts", "export_status")

    op.drop_index(op.f("ix_datasets_celery_task_id"), table_name="datasets")
    op.drop_column("datasets", "celery_task_id")

    # NOTE: do NOT drop the `job_status` enum type here — it is shared with
    # datasets.status, training_jobs.status, and evaluation_runs.status.
