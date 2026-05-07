"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---- Enum types -----------------------------------------------------------
# Created up front so multiple tables can reference the same Postgres type.

task_type_enum = postgresql.ENUM(
    "classification", "tool_calling", "qa",
    name="task_type",
    create_type=False,
)
training_mode_enum = postgresql.ENUM(
    "manual", "hpo",
    name="training_mode",
    create_type=False,
)
dataset_source_enum = postgresql.ENUM(
    "seed", "sdg", "merged",
    name="dataset_source",
    create_type=False,
)
job_status_enum = postgresql.ENUM(
    "pending", "running", "completed", "failed", "cancelled",
    name="job_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    task_type_enum.create(bind, checkfirst=True)
    training_mode_enum.create(bind, checkfirst=True)
    dataset_source_enum.create(bind, checkfirst=True)
    job_status_enum.create(bind, checkfirst=True)

    # ---- projects --------------------------------------------------------
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("task_type", task_type_enum, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
    )
    op.create_index(op.f("ix_projects_name"), "projects", ["name"])
    op.create_index(op.f("ix_projects_task_type"), "projects", ["task_type"])

    # ---- datasets --------------------------------------------------------
    op.create_table(
        "datasets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("task_type", task_type_enum, nullable=False),
        sa.Column("source", dataset_source_enum, nullable=False),
        sa.Column("num_samples", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("storage_uri", sa.String(length=1024), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("generation_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"],
            name=op.f("fk_datasets_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_datasets")),
    )
    op.create_index(op.f("ix_datasets_project_id"), "datasets", ["project_id"])
    op.create_index(op.f("ix_datasets_task_type"), "datasets", ["task_type"])

    # ---- training_jobs ---------------------------------------------------
    op.create_table(
        "training_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("mode", training_mode_enum, nullable=False),
        sa.Column(
            "status", job_status_enum,
            nullable=False,
            server_default=sa.text("'pending'::job_status"),
        ),
        sa.Column("celery_task_id", sa.String(length=64), nullable=True),
        sa.Column("base_model", sa.String(length=256), nullable=False),
        sa.Column("training_name", sa.String(length=200), nullable=True),
        sa.Column("mlflow_experiment_id", sa.String(length=64), nullable=True),
        sa.Column("mlflow_run_id", sa.String(length=64), nullable=True),
        sa.Column("config_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("best_metric_value", sa.Float(), nullable=True),
        sa.Column("best_params_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.String(length=4000), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"],
            name=op.f("fk_training_jobs_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"], ["datasets.id"],
            name=op.f("fk_training_jobs_dataset_id_datasets"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_training_jobs")),
        sa.UniqueConstraint("celery_task_id", name=op.f("uq_training_jobs_celery_task_id")),
    )
    op.create_index(op.f("ix_training_jobs_project_id"), "training_jobs", ["project_id"])
    op.create_index(op.f("ix_training_jobs_dataset_id"), "training_jobs", ["dataset_id"])
    op.create_index(op.f("ix_training_jobs_status"), "training_jobs", ["status"])
    op.create_index(op.f("ix_training_jobs_celery_task_id"), "training_jobs", ["celery_task_id"])
    op.create_index(op.f("ix_training_jobs_mlflow_run_id"), "training_jobs", ["mlflow_run_id"])

    # ---- model_artifacts -------------------------------------------------
    op.create_table(
        "model_artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("training_job_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("base_model", sa.String(length=256), nullable=False),
        sa.Column("mlflow_run_id", sa.String(length=64), nullable=True),
        sa.Column("lora_adapter_uri", sa.String(length=1024), nullable=True),
        sa.Column("gguf_uri", sa.String(length=1024), nullable=True),
        sa.Column("safetensors_uri", sa.String(length=1024), nullable=True),
        sa.Column("size_mb", sa.Float(), nullable=True),
        sa.Column("ollama_model_tag", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["training_job_id"], ["training_jobs.id"],
            name=op.f("fk_model_artifacts_training_job_id_training_jobs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_artifacts")),
        sa.UniqueConstraint("training_job_id", name=op.f("uq_model_artifacts_training_job_id")),
        sa.UniqueConstraint("ollama_model_tag", name=op.f("uq_model_artifacts_ollama_model_tag")),
    )
    op.create_index(
        op.f("ix_model_artifacts_training_job_id"), "model_artifacts", ["training_job_id"]
    )
    op.create_index(
        op.f("ix_model_artifacts_mlflow_run_id"), "model_artifacts", ["mlflow_run_id"]
    )

    # ---- evaluation_runs -------------------------------------------------
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("model_artifact_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("celery_task_id", sa.String(length=64), nullable=True),
        sa.Column(
            "status", job_status_enum,
            nullable=False,
            server_default=sa.text("'pending'::job_status"),
        ),
        sa.Column("metrics_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("llm_judge_score", sa.Float(), nullable=True),
        sa.Column("llm_judge_model", sa.String(length=256), nullable=True),
        sa.Column("error_message", sa.String(length=4000), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["model_artifact_id"], ["model_artifacts.id"],
            name=op.f("fk_evaluation_runs_model_artifact_id_model_artifacts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"], ["datasets.id"],
            name=op.f("fk_evaluation_runs_dataset_id_datasets"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_runs")),
        sa.UniqueConstraint("celery_task_id", name=op.f("uq_evaluation_runs_celery_task_id")),
    )
    op.create_index(
        op.f("ix_evaluation_runs_model_artifact_id"), "evaluation_runs", ["model_artifact_id"]
    )
    op.create_index(
        op.f("ix_evaluation_runs_dataset_id"), "evaluation_runs", ["dataset_id"]
    )
    op.create_index(
        op.f("ix_evaluation_runs_celery_task_id"), "evaluation_runs", ["celery_task_id"]
    )


def downgrade() -> None:
    op.drop_table("evaluation_runs")
    op.drop_table("model_artifacts")
    op.drop_table("training_jobs")
    op.drop_table("datasets")
    op.drop_table("projects")

    bind = op.get_bind()
    job_status_enum.drop(bind, checkfirst=True)
    dataset_source_enum.drop(bind, checkfirst=True)
    training_mode_enum.drop(bind, checkfirst=True)
    task_type_enum.drop(bind, checkfirst=True)
