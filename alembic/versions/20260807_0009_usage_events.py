"""add usage_events table

Revision ID: 0009_usage_events
Revises: 0008_audit_events
Create Date: 2026-08-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_usage_events"
down_revision: str | None = "0008_audit_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.String(length=64), nullable=True),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_usage_events_project_id_projects"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_events")),
    )
    op.create_index(
        op.f("ix_usage_events_created_at"), "usage_events", ["created_at"]
    )
    op.create_index(
        op.f("ix_usage_events_actor_id"), "usage_events", ["actor_id"]
    )
    op.create_index(
        op.f("ix_usage_events_project_id"), "usage_events", ["project_id"]
    )
    op.create_index(
        op.f("ix_usage_events_job_id"), "usage_events", ["job_id"]
    )
    op.create_index(
        "ix_usage_events_project_id_created_at",
        "usage_events",
        ["project_id", "created_at"],
    )
    op.create_index(
        "ix_usage_events_actor_id_created_at",
        "usage_events",
        ["actor_id", "created_at"],
    )


def downgrade() -> None:
    # Only this table's own indexes + the table itself. No shared enum
    # type is touched — usage_events uses a plain string for `outcome`,
    # not an enum.
    op.drop_index("ix_usage_events_actor_id_created_at", table_name="usage_events")
    op.drop_index("ix_usage_events_project_id_created_at", table_name="usage_events")
    op.drop_index(op.f("ix_usage_events_job_id"), table_name="usage_events")
    op.drop_index(op.f("ix_usage_events_project_id"), table_name="usage_events")
    op.drop_index(op.f("ix_usage_events_actor_id"), table_name="usage_events")
    op.drop_index(op.f("ix_usage_events_created_at"), table_name="usage_events")
    op.drop_table("usage_events")
