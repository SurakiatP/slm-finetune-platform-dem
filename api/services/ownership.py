"""Per-resource ownership enforcement, layered on top of `api.core.auth`.

`api/core/auth.py` answers "who is this?"; this module answers "may they
touch this row?" Ownership lives only on `Project.owner_id` — every other
entity reaches it by FK join:

    Dataset / TrainingJob          --(project_id)-->            Project        (1 hop)
    ModelArtifact                  --(training_job_id)-->
                                    TrainingJob --(project_id)--> Project        (2 hops)
    EvaluationRun                  --(model_artifact_id)-->
                                    ModelArtifact --(training_job_id)-->
                                    TrainingJob --(project_id)--> Project        (3 hops)

Two families of helper are provided:

  * `assert_<entity>_access(db, <entity>_id, user)` — load-by-id + ownership
    check in one call, returning the row. Services and routers use this in
    place of the `await db.get(...); if ... is None: raise 404` pattern that
    was already scattered across every service, so the ownership check can't
    be forgotten at a call site.
  * `scope_<entity>_to_owner(stmt, user)` — for `list_*` services. Adds the
    join(s) + `WHERE Project.owner_id == user.id` needed to restrict a
    `select(...)` to the caller's own rows.

**The one rule that matters everywhere in this file**: `user is None` is a
**complete no-op**. Every function here returns immediately (existence-only
check, or the statement unchanged) when `user` is `None`. That is what keeps
phase-1 (`AUTH_REQUIRED=false`, no token on the request) behaviourally
identical to the pre-auth codebase — including byte-for-byte identical SQL
for the `scope_*` helpers, since their `user is None` branch returns the
statement object untouched rather than adding a harmless-looking filter.

**404, not 403, for "exists but belongs to someone else"**: every ownership
failure below is raised with `HTTP_404_NOT_FOUND` and a detail string in the
exact `"{Label} {id} not found"` shape the pre-auth code already used for a
genuinely missing row. This is deliberate, not a bug: if user B could tell
"403 forbidden" (it exists, you can't have it) apart from "404 not found"
(it doesn't exist), the response code alone would let B enumerate the
existence of A's projects/datasets/trainings/models/evaluations. Reserving
403 for this case would leak exactly the information the acceptance
criterion says must not leak.

**`owner_id IS NULL` fails closed**: those rows predate auth, or were
created during phase-1 while no user was attached. Per `Project.owner_id`'s
own `doc=`, once a `user` is present, a null-owner row is invisible to
*everyone*, not visible to everyone — treated exactly like a row owned by
some other user.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.auth import CurrentUser
from api.models.dataset import Dataset
from api.models.evaluation_run import EvaluationRun
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob


def owner_id_for(user: CurrentUser | None) -> str | None:
    """`owner_id` to stamp on a newly-created `Project`.

    `None` when there's no authenticated caller — matches the column's own
    nullable, fail-closed contract (`api/models/project.py`): a project
    created with no `user` (phase-1, or auth disabled) gets `owner_id=NULL`
    and becomes invisible to everyone, itself, once auth is required. Never
    treat `None` here as "public".
    """
    return user.id if user is not None else None


def _not_found(label: str, resource_id: object) -> HTTPException:
    """Build the 404 both the pre-auth "missing row" path and the ownership
    check below raise — same shape, so a probe can't tell them apart.
    """
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"{label} {resource_id} not found",
    )


def _check_owner(
    owner_id: str | None,
    user: CurrentUser | None,
    *,
    label: str,
    resource_id: object,
) -> None:
    """Raise 404 unless `user` is `None` (no-op) or `owner_id == user.id`.

    Centralises the two failure modes that must be indistinguishable from
    the caller's side: `owner_id is None` (fail-closed on legacy/phase-1
    rows) and `owner_id` set to somebody else. Both raise the identical
    `_not_found(label, resource_id)`.
    """
    if user is None:
        return
    if owner_id is None or owner_id != user.id:
        raise _not_found(label, resource_id)


async def _project_owner_id(db: AsyncSession, project_id: UUID) -> str | None:
    """Read just `Project.owner_id` for `project_id`, without loading the row.

    Internal helper for the 1-hop resolvers — they only need the owner to
    compare, so pulling (and discarding) a full `Project` row would be a
    wasted SELECT. Returns `None` both when the project doesn't exist and
    when it exists with a null owner; callers that need to distinguish
    "missing" from "null owner" don't, in practice, care here — either way
    a present `user` fails the `_check_owner` comparison identically.
    """
    return (
        await db.execute(select(Project.owner_id).where(Project.id == project_id))
    ).scalar_one_or_none()


# ---- load-by-id + ownership check ------------------------------------------


async def assert_project_access(
    db: AsyncSession, project_id: UUID, user: CurrentUser | None
) -> Project:
    """Load `Project(project_id)`; 404 if missing or (when `user` is set)
    not owned by `user`. Returns the row.
    """
    project = await db.get(Project, project_id)
    if project is None:
        raise _not_found("Project", project_id)
    _check_owner(project.owner_id, user, label="Project", resource_id=project_id)
    return project


async def assert_dataset_access(
    db: AsyncSession, dataset_id: UUID, user: CurrentUser | None
) -> Dataset:
    """Load `Dataset(dataset_id)`; 404 if missing or its project isn't
    owned by `user` (1-hop: Dataset -> Project). Returns the row.
    """
    dataset = await db.get(Dataset, dataset_id)
    if dataset is None:
        raise _not_found("Dataset", dataset_id)
    if user is not None:
        owner_id = await _project_owner_id(db, dataset.project_id)
        _check_owner(owner_id, user, label="Dataset", resource_id=dataset_id)
    return dataset


async def assert_training_access(
    db: AsyncSession, training_id: UUID, user: CurrentUser | None
) -> TrainingJob:
    """Load `TrainingJob(training_id)`; 404 if missing or its project isn't
    owned by `user` (1-hop: TrainingJob -> Project). Returns the row.
    """
    training = await db.get(TrainingJob, training_id)
    if training is None:
        raise _not_found("Training", training_id)
    if user is not None:
        owner_id = await _project_owner_id(db, training.project_id)
        _check_owner(owner_id, user, label="Training", resource_id=training_id)
    return training


async def assert_model_access(
    db: AsyncSession, model_id: UUID, user: CurrentUser | None
) -> ModelArtifact:
    """Load `ModelArtifact(model_id)`; 404 if missing or not owned by
    `user` (2-hop: ModelArtifact -> TrainingJob -> Project). Returns the row.
    """
    artifact = await db.get(ModelArtifact, model_id)
    if artifact is None:
        raise _not_found("Model", model_id)
    if user is not None:
        owner_id = (
            await db.execute(
                select(Project.owner_id)
                .join(TrainingJob, TrainingJob.project_id == Project.id)
                .where(TrainingJob.id == artifact.training_job_id)
            )
        ).scalar_one_or_none()
        _check_owner(owner_id, user, label="Model", resource_id=model_id)
    return artifact


async def assert_evaluation_access(
    db: AsyncSession, evaluation_id: UUID, user: CurrentUser | None
) -> EvaluationRun:
    """Load `EvaluationRun(evaluation_id)`; 404 if missing or not owned by
    `user` (3-hop: EvaluationRun -> ModelArtifact -> TrainingJob -> Project).
    Returns the row.
    """
    evaluation = await db.get(EvaluationRun, evaluation_id)
    if evaluation is None:
        raise _not_found("Evaluation", evaluation_id)
    if user is not None:
        owner_id = (
            await db.execute(
                select(Project.owner_id)
                .join(TrainingJob, TrainingJob.project_id == Project.id)
                .join(ModelArtifact, ModelArtifact.training_job_id == TrainingJob.id)
                .where(ModelArtifact.id == evaluation.model_artifact_id)
            )
        ).scalar_one_or_none()
        _check_owner(owner_id, user, label="Evaluation", resource_id=evaluation_id)
    return evaluation


# ---- list scoping -----------------------------------------------------------
#
# Each of these is a no-op (returns `stmt` untouched) when `user is None`, so
# a `list_*` call site can unconditionally pipe its statement through the
# matching helper without an `if user:` branch, and phase-1 callers get the
# exact same SQL as before this module existed.


def scope_projects_to_owner(stmt: Select, user: CurrentUser | None) -> Select:
    """Restrict a `select(Project)`/count statement to rows owned by `user`.

    Legacy `owner_id IS NULL` rows are excluded once `user` is set — fail
    closed, matching `assert_project_access` / the column's own doc.
    """
    if user is None:
        return stmt
    return stmt.where(Project.owner_id == user.id)


def scope_datasets_to_owner(stmt: Select, user: CurrentUser | None) -> Select:
    """Restrict a `Dataset`-selecting statement to the caller's projects
    (1-hop join: Dataset -> Project).
    """
    if user is None:
        return stmt
    return stmt.join(Project, Project.id == Dataset.project_id).where(
        Project.owner_id == user.id
    )


def scope_trainings_to_owner(stmt: Select, user: CurrentUser | None) -> Select:
    """Restrict a `TrainingJob`-selecting statement to the caller's projects
    (1-hop join: TrainingJob -> Project).
    """
    if user is None:
        return stmt
    return stmt.join(Project, Project.id == TrainingJob.project_id).where(
        Project.owner_id == user.id
    )


def scope_models_to_owner(stmt: Select, user: CurrentUser | None) -> Select:
    """Restrict a `ModelArtifact`-selecting statement to the caller's
    projects (2-hop join: ModelArtifact -> TrainingJob -> Project).

    Self-contained: joins both `TrainingJob` and `Project` itself rather
    than assuming the caller already joined `TrainingJob` for its own
    filters, so it never collides with (or duplicates) a join the `list_*`
    service added for an unrelated `project_id`/`training_job_id` filter.
    Both FKs it joins on (`ModelArtifact.training_job_id`,
    `TrainingJob.project_id`) are `NOT NULL`, so this INNER JOIN never
    drops a row that would otherwise have matched.
    """
    if user is None:
        return stmt
    return (
        stmt.join(TrainingJob, TrainingJob.id == ModelArtifact.training_job_id)
        .join(Project, Project.id == TrainingJob.project_id)
        .where(Project.owner_id == user.id)
    )


def scope_evaluations_to_owner(stmt: Select, user: CurrentUser | None) -> Select:
    """Restrict an `EvaluationRun`-selecting statement to the caller's
    projects (3-hop join: EvaluationRun -> ModelArtifact -> TrainingJob ->
    Project). Self-contained for the same reason as `scope_models_to_owner`.
    """
    if user is None:
        return stmt
    return (
        stmt.join(ModelArtifact, ModelArtifact.id == EvaluationRun.model_artifact_id)
        .join(TrainingJob, TrainingJob.id == ModelArtifact.training_job_id)
        .join(Project, Project.id == TrainingJob.project_id)
        .where(Project.owner_id == user.id)
    )


__all__ = [
    "owner_id_for",
    "assert_project_access",
    "assert_dataset_access",
    "assert_training_access",
    "assert_model_access",
    "assert_evaluation_access",
    "scope_projects_to_owner",
    "scope_datasets_to_owner",
    "scope_trainings_to_owner",
    "scope_models_to_owner",
    "scope_evaluations_to_owner",
]
