"""Template marketplace persistence and retained training ownership.

Revision ID: 0014_template_marketplace
Revises: 0013_dataset_reuse
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014_template_marketplace"
down_revision = "0013_dataset_reuse"
branch_labels = None
depends_on = None


def _timestamps():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.add_column("projects", sa.Column("template_snapshot", postgresql.JSONB(), nullable=True))
    op.add_column("training_jobs", sa.Column("owner_id", sa.String(64), nullable=True))
    op.add_column("training_jobs", sa.Column("context_snapshot", postgresql.JSONB(), nullable=True))
    op.create_index(op.f("ix_training_jobs_owner_id"), "training_jobs", ["owner_id"])
    # Only a surviving project proves ownership. Pre-existing orphans stay unowned.
    op.execute(sa.text(
        "UPDATE training_jobs AS training SET owner_id = project.owner_id "
        "FROM projects AS project WHERE training.project_id = project.id"
    ))
    op.create_table(
        "template_dataset_versions",
        sa.Column("template_id", sa.String(64), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("definition_sha256", sa.String(64), nullable=False),
        sa.Column("manifest_json", postgresql.JSONB(), nullable=False),
        sa.Column("splits_json", postgresql.JSONB(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("template_id", "version", name=op.f("pk_template_dataset_versions")),
    )
    op.create_table(
        "template_uses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("template_id", sa.String(64), nullable=False),
        sa.Column("template_version", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("response_json", postgresql.JSONB(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_template_uses")),
        sa.UniqueConstraint("user_id", "idempotency_key", name=op.f("uq_template_uses_user_id")),
    )
    op.create_index(op.f("ix_template_uses_template_id"), "template_uses", ["template_id"])
    op.create_table(
        "template_ratings",
        sa.Column("template_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("template_id", "user_id", name=op.f("pk_template_ratings")),
        sa.CheckConstraint("rating BETWEEN 1 AND 5", name=op.f("ck_template_ratings_rating_range")),
    )


def downgrade() -> None:
    op.drop_table("template_ratings")
    op.drop_index(op.f("ix_template_uses_template_id"), table_name="template_uses")
    op.drop_table("template_uses")
    op.drop_table("template_dataset_versions")
    op.drop_index(op.f("ix_training_jobs_owner_id"), table_name="training_jobs")
    op.drop_column("training_jobs", "context_snapshot")
    op.drop_column("training_jobs", "owner_id")
    op.drop_column("projects", "template_snapshot")
