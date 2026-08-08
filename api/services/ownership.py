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

**403, not 404, for "exists but belongs to someone else"** (ADR-012,
superseding the "404, not 403" decision this paragraph used to argue for).
`BACKEND_GAP_ANALYSIS.md:33-34` states the P0 acceptance criterion in plain
terms: user A must get **403** when reaching a resource that belongs to user
B, in every case. The previous version of this module returned 404 instead,
on the theory that 403 would let B enumerate which of A's ids exist. That
theory was correct — it just wasn't the trade the customer's own acceptance
criterion asked for, and it was never signed off as a deviation from it.

Say the trade-off honestly rather than pretending it disappeared: **403
genuinely is an existence oracle.** Once `_check_owner` raises `_forbidden`,
anyone who can authenticate can distinguish "this id exists and isn't
yours" (403) from "this id doesn't exist" (404) for every project, dataset,
training job, model artifact and evaluation run in the system — including
by brute-forcing UUIDs, though the 122 bits of a v4 UUID make that
impractical on its own. We are accepting that leak, not eliminating it,
because the P0 criterion is explicit that the response code must be 403 and
because the alternative (404 for everything) is the exact thing the
criterion was written to rule out. If a future requirement needs both "no
enumeration" and "spec-correct status codes", that needs a new decision
(rate-limiting the ownership-failure path, or a different id scheme), not a
silent revert of this one.

**`owner_id IS NULL` still fails closed, now with 403**: those rows predate
auth, or were created during phase-1 while no user was attached. Per
`Project.owner_id`'s own `doc=`, once a `user` is present, a null-owner row
is invisible to *everyone*, not visible to everyone. The row still exists,
so refusing it is a 403 — the same code as "exists, owned by someone else"
— not a 404; there is no third status for "exists, owned by nobody".
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
    """`owner_id` to stamp on a newly-created `Project`.

    `None` when there's no authenticated caller — matches the column's own
    nullable, fail-closed contract (`api/models/project.py`): a project
    created with no `user` (phase-1, or auth disabled) gets `owner_id=NULL`
    and becomes invisible to everyone, itself, once auth is required. Never
    treat `None` here as "public".
    """
    return user.id if user is not None else None


def _not_found(label: str, resource_id: object) -> HTTPException:
    """The row genuinely doesn't exist. Raised only by the `db.get(...) is
    None` checks below, before `_check_owner` ever runs — so by
    construction this fires when, and only when, there is no row at all.
    """
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"{label} {resource_id} not found",
    )


def _forbidden(label: str, resource_id: object) -> HTTPException:
    """The row exists, and `user` isn't its owner (including `owner_id IS
    NULL`, which belongs to nobody). Only reachable from `_check_owner`,
    which only ever runs after its caller has already confirmed the row
    exists — see every `assert_*_access` below. That ordering is what makes
    403 here correct rather than merely "the code we chose": there is no
    path that raises `_forbidden` for a row that isn't there.
    """
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
    """Raise 403 unless `user` is `None` (no-op) or `owner_id == user.id`.

    Centralises the two failure modes ADR-012 both route to the same
    status: `owner_id is None` (fail-closed on legacy/phase-1 rows) and
    `owner_id` set to somebody else. Both raise `_forbidden(label,
    resource_id)` — 403, not 404, per the P0 acceptance criterion in
    `BACKEND_GAP_ANALYSIS.md:33-34`. Every caller of this function has
    already loaded the row and confirmed it exists (see the module
    docstring), so `_forbidden` never fires for a row that isn't there.
    """
    if user is None:
        return
    if owner_id is None or owner_id != user.id:
        raise _forbidden(label, resource_id)


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


def _bind_log_project(project_id: UUID | None) -> None:
    """Stamp `project_id` onto the logging context for the rest of this request.

    Why here: these five `assert_*_access` functions are the one place every
    ownership-sensitive endpoint passes through, and each already knows (or
    is one indexed hop from) the project. `BACKEND_GAP_ANALYSIS.md`'s P1
    monitoring bullet asks for structured logs carrying "request ID, job ID,
    project ID and tenant ID"; request/user/job were wired in the auth round
    but `project_id` never was — `request_context.set_project_id` existed,
    was listed in `_SETTERS`, was accepted by `bound()`, and had a passing
    test that called it directly, while **no production code path ever
    invoked it**. Same shape as round 2's dead `record_success()`: the
    plumbing was complete and the button was never pressed.

    Deliberately called BEFORE (and outside) each function's `if user is not
    None` ownership branch. With `AUTH_REQUIRED=false` — the platform's
    state today — that branch does not run at all, so binding inside it
    would have produced logs that carry a project only once auth is flipped,
    which is precisely the class of bug this exists to fix.

    Also propagates to Celery: `workers/celery_app.py` stamps the bound
    context onto the message headers at publish time, so a task enqueued by
    a request that resolved a project inherits it without any task body
    change.
    """
    request_context.set_project_id(str(project_id) if project_id else None)


async def assert_project_access(
    db: AsyncSession, project_id: UUID, user: CurrentUser | None
) -> Project:
    """Load `Project(project_id)`; 404 if missing, 403 if it exists and
    (when `user` is set) isn't owned by `user`. Returns the row.
    """
    project = await db.get(Project, project_id)
    if project is None:
        raise _not_found("Project", project_id)
    _bind_log_project(project_id)
    _check_owner(project.owner_id, user, label="Project", resource_id=project_id)
    return project


async def assert_dataset_access(
    db: AsyncSession, dataset_id: UUID, user: CurrentUser | None
) -> Dataset:
    """Load `Dataset(dataset_id)`; 404 if missing, 403 if it exists and its
    project isn't owned by `user` (1-hop: Dataset -> Project). Returns the
    row.
    """
    dataset = await db.get(Dataset, dataset_id)
    if dataset is None:
        raise _not_found("Dataset", dataset_id)
    _bind_log_project(dataset.project_id)
    if user is not None:
        owner_id = await _project_owner_id(db, dataset.project_id)
        _check_owner(owner_id, user, label="Dataset", resource_id=dataset_id)
    return dataset


async def assert_training_access(
    db: AsyncSession, training_id: UUID, user: CurrentUser | None
) -> TrainingJob:
    """Load `TrainingJob(training_id)`; 404 if missing, 403 if it exists and
    its project isn't owned by `user` (1-hop: TrainingJob -> Project).
    Returns the row.
    """
    training = await db.get(TrainingJob, training_id)
    if training is None:
        raise _not_found("Training", training_id)
    _bind_log_project(training.project_id)
    if user is not None:
        owner_id = await _project_owner_id(db, training.project_id)
        _check_owner(owner_id, user, label="Training", resource_id=training_id)
    return training


async def assert_model_access(
    db: AsyncSession, model_id: UUID, user: CurrentUser | None
) -> ModelArtifact:
    """Load `ModelArtifact(model_id)`; 404 if missing, 403 if it exists and
    isn't owned by `user` (2-hop: ModelArtifact -> TrainingJob -> Project).
    Returns the row.
    """
    artifact = await db.get(ModelArtifact, model_id)
    if artifact is None:
        raise _not_found("Model", model_id)
    # One row, two consumers: the owner for the 403 check and the project id
    # for the logging context. Runs unconditionally rather than inside the
    # `user is not None` branch the check used to own — see
    # `_bind_log_project` for why binding must not depend on auth being on.
    # Costs one extra indexed SELECT per call while `AUTH_REQUIRED=false`;
    # once it flips this is the same single query as before.
    row = (
        await db.execute(
            select(Project.id, Project.owner_id)
            .join(TrainingJob, TrainingJob.project_id == Project.id)
            .where(TrainingJob.id == artifact.training_job_id)
        )
    ).one_or_none()
    _bind_log_project(row[0] if row else None)
    if user is not None:
        _check_owner(
            row[1] if row else None, user, label="Model", resource_id=model_id
        )
    return artifact


async def assert_evaluation_access(
    db: AsyncSession, evaluation_id: UUID, user: CurrentUser | None
) -> EvaluationRun:
    """Load `EvaluationRun(evaluation_id)`; 404 if missing, 403 if it exists
    and isn't owned by `user` (3-hop: EvaluationRun -> ModelArtifact ->
    TrainingJob -> Project). Returns the row.
    """
    evaluation = await db.get(EvaluationRun, evaluation_id)
    if evaluation is None:
        raise _not_found("Evaluation", evaluation_id)
    # Same shape as `assert_model_access` above — see its comment.
    row = (
        await db.execute(
            select(Project.id, Project.owner_id)
            .join(TrainingJob, TrainingJob.project_id == Project.id)
            .join(ModelArtifact, ModelArtifact.training_job_id == TrainingJob.id)
            .where(ModelArtifact.id == evaluation.model_artifact_id)
        )
    ).one_or_none()
    _bind_log_project(row[0] if row else None)
    if user is not None:
        _check_owner(
            row[1] if row else None,
            user,
            label="Evaluation",
            resource_id=evaluation_id,
        )
    return evaluation


# ---- list scoping -----------------------------------------------------------
#
# Each of these is a no-op (returns `stmt` untouched) when `user is None`, so
# a `list_*` call site can unconditionally pipe its statement through the
# matching helper without an `if user:` branch, and phase-1 callers get the
# exact same SQL as before this module existed.
#
# ADR-012 does NOT touch these. `_check_owner`/`_forbidden` above answer "may
# `user` load *this specific row*" — there's a concrete id to be 403 about.
# A `scope_*` helper answers a different question, "which rows does a list
# query return", by adding a `WHERE Project.owner_id = user.id` filter; a row
# that fails it is never rendered into a response at all, it is just absent
# from the page. There is no id in play for a 403 to attach to and no
# request to fail — returning 403 for a *list* endpoint would be a category
# error, not a stricter check. Silently filtering is the correct behaviour
# here regardless of which status code the single-resource endpoints use, so
# this asymmetry with the `assert_*_access` family above is intentional, not
# a spot the 404→403 change was missed.


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
