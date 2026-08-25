"""Resolve which user owns a Celery job — the WebSocket authorization gate.

A `job_id` is the public Celery task id handed to clients as the
`/ws/jobs/{job_id}` path parameter (and, before that, as the `job_id` field of
whichever `*_service.py` response started the job). It can name a row in any
of four tables — `Dataset` (SDG), `TrainingJob`, `EvaluationRun`, or
`ModelArtifact` (export) — because `feat/be-fe-gap001` added a
`celery_task_id`-shaped column to each independently. `BACKEND_GAP_ANALYSIS.md`
is explicit that this id must stop being treated as a secret capability, so
`api/routers/websocket.py` needs to know *whose* job it is before it lets
anyone subscribe to it.

This module answers exactly that question, and nothing else: given a
`job_id`, find the row it names (checking all four tables) and walk foreign
keys up to the owning `Project.owner_id`. It is read-only and has no HTTP
concerns — each caller decides what status/close code to use for "not
found" vs. "found but not yours".

**ADR-012 (404→403 for owner mismatch) touches both callers of
`resolve_job_owner`, and touches them differently — this module's own
`JobOwnerResult(found, owner_id)` shape doesn't change at all, because it
already carries enough information for each caller to make its own call:**

  * `api/routers/jobs.py` (`GET /api/v1/jobs/{job_id}/progress`, a REST
    endpoint) now raises 404 when `found=False` and 403 when `found=True`
    with an owner mismatch — same split as `ownership.py`'s `assert_*_access`
    family, since this is the REST twin of the ownership check they do.
  * `api/routers/websocket.py` (`/ws/jobs/{job_id}`) deliberately keeps a
    single close code (`4403`) for both cases. Two reasons, not one: (a) the
    WS path was explicitly carved out of this round's scope — 4401/4403 are
    unobservable to browsers today (`ws.onclose` reports `1006` for a
    server-initiated close in most cases), so splitting them buys nothing
    until that's fixed, tracked separately; (b) even if it were observable,
    a close code is a curter signal than an HTTP status with a body, and
    collapsing "unknown job" with "not yours" there was already deliberate
    per ADR-009, independent of the REST 404-vs-403 question.

If `resolve_job_owner` itself ever grew HTTP- or close-code-shaped return
values, that would force the WS and REST paths to agree again by
construction — which is exactly why it doesn't: returning the plain
`(found, owner_id)` tuple is what lets the two callers diverge without this
module having to know that they do.

Ownership joins (mirrors the depths documented in the branch plan):
  • Dataset / TrainingJob         -> Project                       (1 hop)
  • ModelArtifact (export)        -> TrainingJob -> Project         (2 hops)
  • EvaluationRun                 -> ModelArtifact -> TrainingJob -> Project (3 hops)
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.dataset import Dataset
from api.models.evaluation_run import EvaluationRun
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob


@dataclass(frozen=True, slots=True)
class JobOwnerResult:
    """Outcome of resolving a `job_id` to the `Project` that owns it.

    `found=False` means no Dataset/TrainingJob/EvaluationRun/ModelArtifact
    anywhere references this `job_id` at all — an unknown id.

    `found=True, owner_id=None` means the job WAS found, but its `Project`
    predates auth (`owner_id IS NULL`). Per `api/models/project.py`'s own
    documented rule, a null `owner_id` is visible to nobody once a caller is
    authenticated — callers of this module must treat that the same as a
    mismatch, not as "public".
    """

    found: bool
    owner_id: str | None


async def resolve_job_owner(db: AsyncSession, job_id: str) -> JobOwnerResult:
    """Find the `Project.owner_id` for whichever job `job_id` names.

    Checks, in order, `Dataset.celery_task_id` (falling back to the legacy
    `generation_metadata['celery_task_id']` JSONB value for older rows that
    predate the dedicated column), `TrainingJob.celery_task_id`,
    `EvaluationRun.celery_task_id`, and `ModelArtifact.export_celery_task_id`.
    Returns on the first match; `job_id` is expected to be unique to one job
    across all four tables in practice (SDG generation guarantees this only
    loosely — see `Dataset.celery_task_id`'s own docstring — but two
    different jobs colliding on the same Celery task id is not a case this
    function can or needs to disambiguate).
    """
    # 1. Dataset (SDG) -> Project.
    stmt = (
        select(Project.owner_id)
        .select_from(Dataset)
        .join(Project, Project.id == Dataset.project_id)
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

    # 2. TrainingJob -> Project.
    #
    # Outer join, not inner: TrainingJob.project_id is nullable (orphaned
    # runs whose Project was deleted -- migration 0012_training_decouple).
    # An INNER JOIN would make an orphaned-but-still-running training's
    # celery_task_id resolve to found=False ("no such job") instead of the
    # correct found=True, owner_id=None ("found, but no owner to check
    # against") -- the same shape this module already uses for a Project
    # that predates auth (see JobOwnerResult's docstring), and the two
    # cases should be indistinguishable to callers for the same reason.
    stmt = (
        select(TrainingJob.id, Project.owner_id)
        .select_from(TrainingJob)
        .outerjoin(Project, Project.id == TrainingJob.project_id)
        .where(TrainingJob.celery_task_id == job_id)
        .limit(1)
    )
    row = (await db.execute(stmt)).first()
    if row is not None:
        return JobOwnerResult(found=True, owner_id=row[1])

    # 3. EvaluationRun -> ModelArtifact -> TrainingJob -> Project.
    # Same nullability split as ownership.py's scope_evaluations_to_owner:
    # the first two joins are NOT NULL FKs and stay INNER JOINs; the last
    # hop (TrainingJob.project_id) is nullable, so it's an outer join, for
    # the same orphan reasoning as leg 2 above.
    stmt = (
        select(EvaluationRun.id, Project.owner_id)
        .select_from(EvaluationRun)
        .join(ModelArtifact, ModelArtifact.id == EvaluationRun.model_artifact_id)
        .join(TrainingJob, TrainingJob.id == ModelArtifact.training_job_id)
        .outerjoin(Project, Project.id == TrainingJob.project_id)
        .where(EvaluationRun.celery_task_id == job_id)
        .limit(1)
    )
    row = (await db.execute(stmt)).first()
    if row is not None:
        return JobOwnerResult(found=True, owner_id=row[1])

    # 4. ModelArtifact (export) -> TrainingJob -> Project.
    # Same split: ModelArtifact.training_job_id is NOT NULL (inner join);
    # TrainingJob.project_id is nullable (outer join), same orphan
    # reasoning as legs 2 and 3 above.
    stmt = (
        select(ModelArtifact.id, Project.owner_id)
        .select_from(ModelArtifact)
        .join(TrainingJob, TrainingJob.id == ModelArtifact.training_job_id)
        .outerjoin(Project, Project.id == TrainingJob.project_id)
        .where(ModelArtifact.export_celery_task_id == job_id)
        .limit(1)
    )
    row = (await db.execute(stmt)).first()
    if row is not None:
        return JobOwnerResult(found=True, owner_id=row[1])

    return JobOwnerResult(found=False, owner_id=None)


__all__ = ["JobOwnerResult", "resolve_job_owner"]
