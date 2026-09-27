"""add deployments and api_keys tables

Revision ID: 0015_deployments_api_keys
Revises: 0014_template_marketplace
Create Date: 2026-09-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_deployments_api_keys"
down_revision: str | None = "0014_template_marketplace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The `job_status` Postgres enum type was already created by 0001_initial for
# training_jobs.status / evaluation_runs.status (and reused since by
# datasets.status and model_artifacts.export_status). Reference it here
# without re-creating it.
_JOB_STATUS_ENUM = postgresql.ENUM(
    "pending", "running", "completed", "failed", "cancelled",
    name="job_status",
    create_type=False,
)


def upgrade() -> None:
    op.create_table(
        "deployments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("model_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "status", _JOB_STATUS_ENUM, nullable=False, server_default="pending",
        ),
        sa.Column("rate_limit_per_min", sa.Integer(), nullable=False),
        sa.Column("celery_task_id", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=4000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["model_artifact_id"], ["model_artifacts.id"],
            name=op.f("fk_deployments_model_artifact_id_model_artifacts"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deployments")),
    )
    op.create_index(op.f("ix_deployments_owner_id"), "deployments", ["owner_id"])
    op.create_index(op.f("ix_deployments_model_artifact_id"), "deployments", ["model_artifact_id"])
    op.create_index(op.f("ix_deployments_status"), "deployments", ["status"])
    op.create_index(op.f("ix_deployments_celery_task_id"), "deployments", ["celery_task_id"])

    op.create_table(
        "api_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("last4", sa.String(length=4), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("key_hash", name=op.f("uq_api_keys_key_hash")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_keys")),
    )
    op.create_index(op.f("ix_api_keys_owner_id"), "api_keys", ["owner_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_api_keys_owner_id"), table_name="api_keys")
    op.drop_table("api_keys")

    op.drop_index(op.f("ix_deployments_celery_task_id"), table_name="deployments")
    op.drop_index(op.f("ix_deployments_status"), table_name="deployments")
    op.drop_index(op.f("ix_deployments_model_artifact_id"), table_name="deployments")
    op.drop_index(op.f("ix_deployments_owner_id"), table_name="deployments")
    op.drop_table("deployments")

    # NOTE: do NOT drop the `job_status` enum type here — it is shared with
    # datasets.status, training_jobs.status, evaluation_runs.status, and
    # model_artifacts.export_status.
