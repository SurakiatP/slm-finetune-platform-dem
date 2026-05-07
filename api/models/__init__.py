"""SQLAlchemy 2.0 ORM models.

Importing this package is enough for Alembic autogenerate to discover all
tables — `Base.metadata` is fully populated.
"""

from __future__ import annotations

from api.models.base import Base, TimestampMixin
from api.models.dataset import Dataset
from api.models.evaluation_run import EvaluationRun
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob

__all__ = [
    "Base",
    "TimestampMixin",
    "Project",
    "Dataset",
    "TrainingJob",
    "ModelArtifact",
    "EvaluationRun",
]
