"""Resource ownership, including retained datasets and training descendants.

Project, Dataset and TrainingJob carry owner_id directly. Models and evaluations
resolve through TrainingJob, so deleting a project does not erase ownership.
Unknown owners fail closed for authenticated callers. With user=None, checks
remain existence-only and list scopes return the original statement unchanged.

ADR-012 requires 403 for an existing inaccessible row and 404 for a missing row;
this deliberately accepts the existence oracle described in that decision.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core import request_context
from api.core.auth import CurrentUser
from api.models.dataset import Dataset
from api.models.evaluation_run import EvaluationRun
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob


def owner_id_for(user: CurrentUser | None) -> str | None:
    """Stamp the authenticated identity; NULL is never a public owner."""
    return user.id if user is not None else None


def _not_found(label: str, resource_id: object) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"{label} {resource_id} not found",
    )


def _forbidden(label: str, resource_id: object) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"{label} {resource_id} is not accessible",
    )


def _check_owner(
    owner_id: str | None,
    user: CurrentUser | None,
    *,
    label: str,
    resource_id: object,
) -> None:
    if user is not None and (owner_id is None or owner_id != user.id):
        raise _forbidden(label, resource_id)


def _bind_log_project(project_id: UUID | None) -> None:
    """Bind even with auth off, so request and Celery logs retain context."""
    request_context.set_project_id(str(project_id) if project_id else None)


async def assert_project_access(
    db: AsyncSession, project_id: UUID, user: CurrentUser | None
) -> Project:
    project = await db.get(Project, project_id)
    if project is None:
        raise _not_found("Project", project_id)
    _bind_log_project(project_id)
    _check_owner(project.owner_id, user, label="Project", resource_id=project_id)
    return project


async def assert_dataset_access(
    db: AsyncSession, dataset_id: UUID, user: CurrentUser | None
) -> Dataset:
    dataset = await db.get(Dataset, dataset_id)
    if dataset is None:
        raise _not_found("Dataset", dataset_id)
    _bind_log_project(dataset.project_id)
    _check_owner(dataset.owner_id, user, label="Dataset", resource_id=dataset_id)
    return dataset


async def assert_training_access(
    db: AsyncSession, training_id: UUID, user: CurrentUser | None
) -> TrainingJob:
    training = await db.get(TrainingJob, training_id)
    if training is None:
        raise _not_found("Training", training_id)
    _bind_log_project(training.project_id)
    _check_owner(training.owner_id, user, label="Training", resource_id=training_id)
    return training


async def assert_model_access(
    db: AsyncSession, model_id: UUID, user: CurrentUser | None
) -> ModelArtifact:
    artifact = await db.get(ModelArtifact, model_id)
    if artifact is None:
        raise _not_found("Model", model_id)
    row = (
        await db.execute(
            select(TrainingJob.project_id, TrainingJob.owner_id).where(
                TrainingJob.id == artifact.training_job_id
            )
        )
    ).one_or_none()
    _bind_log_project(row[0] if row else None)
    _check_owner(row[1] if row else None, user, label="Model", resource_id=model_id)
    return artifact


async def assert_evaluation_access(
    db: AsyncSession, evaluation_id: UUID, user: CurrentUser | None
) -> EvaluationRun:
    evaluation = await db.get(EvaluationRun, evaluation_id)
    if evaluation is None:
        raise _not_found("Evaluation", evaluation_id)
    row = (
        await db.execute(
            select(TrainingJob.project_id, TrainingJob.owner_id)
            .join(ModelArtifact, ModelArtifact.training_job_id == TrainingJob.id)
            .where(ModelArtifact.id == evaluation.model_artifact_id)
        )
    ).one_or_none()
    _bind_log_project(row[0] if row else None)
    _check_owner(row[1] if row else None, user, label="Evaluation", resource_id=evaluation_id)
    return evaluation


def scope_projects_to_owner(stmt: Select, user: CurrentUser | None) -> Select:
    if user is None:
        return stmt
    return stmt.where(Project.owner_id == user.id)


def scope_datasets_to_owner(stmt: Select, user: CurrentUser | None) -> Select:
    if user is None:
        return stmt
    return stmt.where(Dataset.owner_id == user.id)


def scope_trainings_to_owner(stmt: Select, user: CurrentUser | None) -> Select:
    if user is None:
        return stmt
    return stmt.where(TrainingJob.owner_id == user.id)


def scope_models_to_owner(stmt: Select, user: CurrentUser | None) -> Select:
    if user is None:
        return stmt
    return stmt.join(TrainingJob, TrainingJob.id == ModelArtifact.training_job_id).where(
        TrainingJob.owner_id == user.id
    )


def scope_evaluations_to_owner(stmt: Select, user: CurrentUser | None) -> Select:
    if user is None:
        return stmt
    return (
        stmt.join(ModelArtifact, ModelArtifact.id == EvaluationRun.model_artifact_id)
        .join(TrainingJob, TrainingJob.id == ModelArtifact.training_job_id)
        .where(TrainingJob.owner_id == user.id)
    )


__all__ = [
    "assert_dataset_access",
    "assert_evaluation_access",
    "assert_model_access",
    "assert_project_access",
    "assert_training_access",
    "owner_id_for",
    "scope_datasets_to_owner",
    "scope_evaluations_to_owner",
    "scope_models_to_owner",
    "scope_projects_to_owner",
    "scope_trainings_to_owner",
]
