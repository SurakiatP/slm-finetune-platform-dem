"""SQLAlchemy 2.0 ORM models.

Importing this package is enough for Alembic autogenerate to discover all
tables — `Base.metadata` is fully populated.
"""

from __future__ import annotations

from api.models.api_key import ApiKey
from api.models.audit_event import AuditEvent
from api.models.base import Base, TimestampMixin
from api.models.dataset import Dataset
from api.models.deployment import Deployment
from api.models.evaluation_run import EvaluationRun
from api.models.identity_link import IdentityLink
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.template import TemplateDatasetVersion, TemplateRating, TemplateUse
from api.models.training_job import TrainingJob
from api.models.usage_event import UsageEvent

__all__ = [
    "ApiKey",
    "AuditEvent",
    "Base",
    "Dataset",
    "Deployment",
    "EvaluationRun",
    "IdentityLink",
    "ModelArtifact",
    "Project",
    "TemplateDatasetVersion",
    "TemplateRating",
    "TemplateUse",
    "TimestampMixin",
    "TrainingJob",
    "UsageEvent",
]
