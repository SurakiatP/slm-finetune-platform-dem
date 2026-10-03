"""add identity_links table

Revision ID: 0016_identity_links
Revises: 0015_deployments_api_keys
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016_identity_links"
down_revision: str | None = "0015_deployments_api_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "identity_links",
        sa.Column("issuer", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("issuer", "subject", name=op.f("pk_identity_links")),
    )
    op.create_index(op.f("ix_identity_links_actor_id"), "identity_links", ["actor_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_identity_links_actor_id"), table_name="identity_links")
    op.drop_table("identity_links")
