"""Resolve job owners for REST progress and WebSocket authorization.

Use the retained Dataset/TrainingJob owner, never a possibly deleted project.
REST distinguishes unknown jobs (404) from inaccessible jobs (403); WebSocket
uses 4403 for both. Unknown owners are not public.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.dataset import Dataset
from api.models.deployment import Deployment
from api.models.evaluation_run import EvaluationRun
from api.models.model_artifact import ModelArtifact
from api.models.training_job import TrainingJob


@dataclass(frozen=True, slots=True)
class JobOwnerResult:
    found: bool
    owner_id: str | None


async def resolve_job_owner(db: AsyncSession, job_id: str) -> JobOwnerResult:
    """Check SDG, training, evaluation and export jobs, returning the first match."""
    stmt = (
        select(Dataset.owner_id)
        .where(
            or_(
                Dataset.celery_task_id == job_id,
                Dataset.generation_metadata["celery_task_id"].astext == job_id,
            )
        )
        .limit(1)
    )
    row = (await db.execute(stmt)).first()
    if row is not None:
        return JobOwnerResult(found=True, owner_id=row[0])

    stmt = select(TrainingJob.owner_id).where(TrainingJob.celery_task_id == job_id).limit(1)
    row = (await db.execute(stmt)).first()
    if row is not None:
        return JobOwnerResult(found=True, owner_id=row[0])

    stmt = (
        select(TrainingJob.owner_id)
        .select_from(EvaluationRun)
        .join(ModelArtifact, ModelArtifact.id == EvaluationRun.model_artifact_id)
        .join(TrainingJob, TrainingJob.id == ModelArtifact.training_job_id)
        .where(EvaluationRun.celery_task_id == job_id)
        .limit(1)
    )
    row = (await db.execute(stmt)).first()
    if row is not None:
        return JobOwnerResult(found=True, owner_id=row[0])

    stmt = (
        select(TrainingJob.owner_id)
        .select_from(ModelArtifact)
        .join(TrainingJob, TrainingJob.id == ModelArtifact.training_job_id)
        .where(ModelArtifact.export_celery_task_id == job_id)
        .limit(1)
    )
    row = (await db.execute(stmt)).first()
    if row is not None:
        return JobOwnerResult(found=True, owner_id=row[0])

    # Deployment.owner_id is direct (no project/training hop to walk) —
    # unlike the four resources above, a deployment carries its own owner.
    stmt = select(Deployment.owner_id).where(Deployment.celery_task_id == job_id).limit(1)
    row = (await db.execute(stmt)).first()
    if row is not None:
        return JobOwnerResult(found=True, owner_id=row[0])

    return JobOwnerResult(found=False, owner_id=None)


__all__ = ["JobOwnerResult", "resolve_job_owner"]
