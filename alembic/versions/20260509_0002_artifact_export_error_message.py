"""add model_artifacts.export_error_message (B7)

Revision ID: 0002_export_error
Revises: 0001_initial
Create Date: 2026-05-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_export_error"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model_artifacts",
        sa.Column("export_error_message", sa.String(length=4000), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("model_artifacts", "export_error_message")
