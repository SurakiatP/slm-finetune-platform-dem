"""add audit_events table

Revision ID: 0008_audit_events
Revises: 0007_project_owner_id
Create Date: 2026-08-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_audit_events"
down_revision: str | None = "0007_project_owner_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.String(length=64), nullable=True),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=50), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_audit_events_project_id_projects"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(
        op.f("ix_audit_events_created_at"), "audit_events", ["created_at"]
    )
    op.create_index(
        op.f("ix_audit_events_actor_id"), "audit_events", ["actor_id"]
    )
    op.create_index(
        op.f("ix_audit_events_project_id"), "audit_events", ["project_id"]
    )
    op.create_index(
        op.f("ix_audit_events_request_id"), "audit_events", ["request_id"]
    )
    op.create_index(
        "ix_audit_events_project_id_created_at",
        "audit_events",
        ["project_id", "created_at"],
    )


def downgrade() -> None:
    # Only this table's own indexes + the table itself. The shared
    # `job_status` Postgres enum type is not touched — audit_events uses
    # plain strings for `outcome`, not an enum.
    op.drop_index("ix_audit_events_project_id_created_at", table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_request_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_project_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_actor_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_created_at"), table_name="audit_events")
    op.drop_table("audit_events")
