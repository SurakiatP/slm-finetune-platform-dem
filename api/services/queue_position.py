"""Display-only, project-level GPU queue-position service.

This module answers one question for the UI: "where does my project sit
in the GPU queue right now?" It does NOT touch scheduling in any way —
Celery still dispatches PENDING rows in whatever order its broker gives
them (FIFO per queue, subject to worker availability), and nothing here
changes that. `compute_queue` is a pure read: it inspects current DB state
and derives an *ordinal* a human can look at ("you are #3"), nothing more.
If this module vanished entirely, job execution order would be unchanged.

SDG is deliberately excluded (user decision). `api/services/quota.py`
folds SDG (`Dataset.status`) into its own bucket because SDG jobs also
compete for API-rate/cost budget and get their own cap; queue *display*
was scoped by the user to GPU contention only — SDG generation runs on
OpenRouter's infrastructure, not the local RTX 3060, so a user watching
"my GPU queue position" would find an SDG row in the count confusing
("why am I #2 behind a job that isn't even touching the GPU?"). If a
future task wants an SDG queue display too, it should be a parallel,
separately-labelled bucket, not folded into this one.

Same tri-table "GPU bucket" as `api/services/quota.py`, and the same
`_IN_FLIGHT = (PENDING, RUNNING)` filter — reused directly from there so
the two modules can never drift on what counts as "in flight". The three
join depths back to `Project` are IDENTICAL to quota.py's, and are
re-derived independently here (not just trusted) from the same FK
declarations in api/models/{training_job,model_artifact,evaluation_run,
project}.py, matching the provenance note quota.py:119-124 already makes
about `api/services/job_ownership.py`:

  TrainingJob   -> Project                                  (1 hop)
    TrainingJob.project_id is a direct FK to projects.id.
  ModelArtifact -> TrainingJob -> Project                    (2 hops)
    ModelArtifact.training_job_id -> training_jobs.id, then
    TrainingJob.project_id -> projects.id.
  EvaluationRun -> ModelArtifact -> TrainingJob -> Project    (3 hops)
    EvaluationRun.model_artifact_id -> model_artifacts.id, then the two
    hops above. EvaluationRun has no project_id of its own.

Ordering timestamp per leg, and why:
  - training leg: `TrainingJob.created_at` — the row is created at submit
    time, so this is exactly "when did this training enter the queue".
  - export leg: `ModelArtifact.updated_at`, NOT `ModelArtifact.created_at`.
    `created_at` is stamped when the artifact row is first created, which
    is when *training finished* — long before (often) an export is ever
    requested. There is no dedicated "export submitted at" column (see
    quota.py:14-20 — `ModelArtifact` has no generic `status`/timeline
    columns beyond what migration 0006 added for `export_status`), so
    this module reuses `updated_at`: submitting an export flips
    `export_status` PENDING, and that column write touches `updated_at`
    via the `TimestampMixin.onupdate=func.now()` hook, landing it
    approximately at submit time. This is a real trade-off: any other
    field write on the same artifact row between submits would also bump
    `updated_at` and could nudge the ordinal. Accepted deliberately
    because (a) this is a *display* ordinal, not a scheduling input —
    being off by one slot for a few seconds has no functional
    consequence, and (b) adding a dedicated `export_submitted_at` column
    would require an Alembic migration, which is out of scope for a
    read-only display feature. Revisit if export ordering complaints ever
    come in.
  - eval leg: `EvaluationRun.created_at` — same reasoning as the training
    leg, the row is created at submit time.

Why cancelled PENDING rows drop out on their own: the project's cancel
services (see `api/services/*` cancel endpoints for training/export/eval)
flip the row's status to CANCELLED synchronously as part of the cancel
request, which takes it out of `_IN_FLIGHT` immediately. Because this
module computes fresh from the DB on every call (no caching, no
snapshotting), a cancelled job disappears from the queue on the very next
read — no separate bookkeeping needed here.

Why one Python-side aggregation pass instead of three separate ranking
queries: row volume in this bucket is bounded by the same tiny global GPU
quota cap `quota.py` enforces (a handful of jobs at most, see
`settings.quota_max_gpu_jobs_global`), so pulling every in-flight row
into Python and ranking it there is cheap, and it keeps the whole
computation portable to the aiosqlite in-memory engine used by the async
test fixtures (window functions / `RANK() OVER (...)` support varies
across the Postgres-vs-sqlite dialects this codebase runs tests against;
plain Python `sorted()` has no such divergence).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.evaluation_run import EvaluationRun
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.enums import JobStatus

# Same bucket membership as api/services/quota.py's `_IN_FLIGHT` — reused
# by value (not imported) to keep this module import-light; see quota.py's
# own docstring for why PENDING/RUNNING are the only two states that count.
_IN_FLIGHT = (JobStatus.PENDING, JobStatus.RUNNING)


@dataclass(frozen=True)
class ProjectQueueInfo:
    """Display payload for one project's place in the GPU queue.

    `queue_position` / `owner_queue_position` are 1-based (position "1"
    means "next up"), matching how a human reads a queue ("you are #1"),
    not 0-based array indexing. Both are `None` exactly when
    `queue_state == "processing"` — a project with something actively
    RUNNING isn't waiting in line, it's being served, so a queue position
    for it is meaningless.
    """

    queue_state: Literal["processing", "queued"]
    queue_position: int | None
    owner_queue_position: int | None


async def compute_queue(db: AsyncSession) -> dict[UUID, ProjectQueueInfo]:
    """Compute every project's GPU queue state in one pass.

    Returns a dict keyed by `project_id`; projects with no in-flight GPU
    row at all (nothing PENDING/RUNNING across the three legs) are simply
    absent from the returned dict — callers should treat a missing key as
    "not in the queue", not as an error. `project_queue_info` below wraps
    that lookup.

    Algorithm:
      1. Build three aligned SELECTs (project_id, owner_id, status,
         timestamp) — one per leg, joined to `Project` at the depth
         documented in the module docstring — each already filtered to
         `_IN_FLIGHT` so no completed/failed/cancelled rows are pulled in
         at all.
      2. `union_all` them into a single statement and execute once. (An
         `_IN_FLIGHT`-filtered row set is small — see the module
         docstring's aggregation rationale — so one round trip beats
         three.)
      3. Aggregate in Python per `project_id`:
           - any row RUNNING -> ("processing", None, None). A project can
             only ever have at most one thing actively holding the GPU at
             a time in practice (the shared cap in quota.py enforces
             exactly one in-flight GPU job per actor by default), but this
             check is written as "any" rather than "the one row" so it
             stays correct even if that cap setting changes later.
           - otherwise (only PENDING rows) -> the project is queued.
             Queued projects are ranked by `(min(timestamp across its
             PENDING rows), str(project_id))` — the timestamp tiebreaks
             chronologically (earliest submit first, matching intuitive
             FIFO expectations), and `str(project_id)` is a pure
             tie-breaker for full determinism when two projects'
             timestamps land in the same DB-clock tick (server_default
             `func.now()` has second-level granularity on some deployment
             configs), not a meaningful ordering signal itself.
      4. `queue_position` is the 1-based index of a queued project in the
         GLOBAL ordering above. `owner_queue_position` is the 1-based
         index of that same project within the subsequence of queued
         projects sharing its `owner_id`, using the identical sort key.
         Per `quota.py:47-58`'s precedent for anonymous callers,
         `owner_id IS NULL` is treated as ONE shared group (not "each
         anonymous project has no group") — under today's
         `AUTH_REQUIRED=false` setting every project's `owner_id` is
         `None`, so that single group contains every queued project and
         `owner_queue_position == queue_position` for all of them. This
         is intentional and inert dead weight until `AUTH_REQUIRED` flips
         to `true` project-wide, exactly like the precedent it mirrors.
    """
    # Outer joins to Project, not inner: `TrainingJob.project_id` is nullable
    # (orphaned runs whose Project was deleted — migration
    # 0012_training_decouple, see api/services/ownership.py's orphan-policy
    # section). This module is display-only and keyed by `project_id`
    # itself (see the dict return type), so an INNER JOIN wouldn't just
    # mis-attribute an orphan's owner — it would drop the orphaned run from
    # the queue display ENTIRELY, understating queue depth for anyone still
    # waiting behind it. `Project.owner_id` comes back NULL for an orphan
    # row here, which lands it in the same shared null-owner group
    # `owner_queue_position` already treats every anonymous project as
    # (see the module docstring) — no extra branching needed.
    training_stmt = (
        select(
            TrainingJob.project_id.label("project_id"),
            Project.owner_id.label("owner_id"),
            TrainingJob.status.label("status"),
            TrainingJob.created_at.label("ts"),
        )
        .select_from(TrainingJob)
        .outerjoin(Project, Project.id == TrainingJob.project_id)
        .where(TrainingJob.status.in_(_IN_FLIGHT))
    )
    export_stmt = (
        select(
            TrainingJob.project_id.label("project_id"),
            Project.owner_id.label("owner_id"),
            ModelArtifact.export_status.label("status"),
            # Trade-off documented in the module docstring: `updated_at`
            # (not `created_at`, which is training-completion time) is the
            # closest available proxy for "export submitted at".
            ModelArtifact.updated_at.label("ts"),
        )
        .select_from(ModelArtifact)
        .join(TrainingJob, TrainingJob.id == ModelArtifact.training_job_id)
        .outerjoin(Project, Project.id == TrainingJob.project_id)
        .where(ModelArtifact.export_status.in_(_IN_FLIGHT))
    )
    eval_stmt = (
        select(
            TrainingJob.project_id.label("project_id"),
            Project.owner_id.label("owner_id"),
            EvaluationRun.status.label("status"),
            EvaluationRun.created_at.label("ts"),
        )
        .select_from(EvaluationRun)
        .join(ModelArtifact, ModelArtifact.id == EvaluationRun.model_artifact_id)
        .join(TrainingJob, TrainingJob.id == ModelArtifact.training_job_id)
        .outerjoin(Project, Project.id == TrainingJob.project_id)
        .where(EvaluationRun.status.in_(_IN_FLIGHT))
    )

    combined = union_all(training_stmt, export_stmt, eval_stmt)
    rows = (await db.execute(combined)).all()

    # ---- Python-side aggregation (see module docstring for why) --------

    # project_id -> whether it has at least one RUNNING row anywhere.
    running_projects: set[UUID] = set()
    # project_id -> (owner_id, earliest PENDING timestamp seen for it).
    # The union_all already aligned column types/labels across the three
    # legs, so this is plain row scanning, no further reconciliation.
    pending_earliest: dict[UUID, tuple[str | None, object]] = {}

    for row in rows:
        project_id = row.project_id
        if row.status == JobStatus.RUNNING:
            running_projects.add(project_id)
            continue
        # row.status == JobStatus.PENDING (the only other member of
        # _IN_FLIGHT); track the earliest timestamp per project.
        existing = pending_earliest.get(project_id)
        if existing is None or row.ts < existing[1]:
            pending_earliest[project_id] = (row.owner_id, row.ts)

    result: dict[UUID, ProjectQueueInfo] = {
        project_id: ProjectQueueInfo(
            queue_state="processing", queue_position=None, owner_queue_position=None
        )
        for project_id in running_projects
    }

    # Only genuinely queued projects (PENDING-only, no RUNNING row at all)
    # get a rank. A project with both a PENDING and a RUNNING row (e.g.
    # training running while an earlier export request is still queued)
    # is "processing", full stop — it's already occupying the GPU, so it
    # was excluded above and must not also get a queue position here.
    queued = {
        project_id: entry
        for project_id, entry in pending_earliest.items()
        if project_id not in running_projects
    }

    # Global order: (earliest timestamp, str(project_id)) ascending.
    global_order = sorted(queued.items(), key=lambda item: (item[1][1], str(item[0])))

    # Per-owner order, built by re-filtering the already-sorted global
    # order — this keeps the per-owner subsequence in the same relative
    # order as the global one without a second sort, and treats
    # owner_id=None as one shared anonymous group per quota.py precedent.
    owner_position_counters: dict[str | None, int] = {}
    owner_positions: dict[UUID, int] = {}
    for project_id, (owner_id, _ts) in global_order:
        owner_position_counters[owner_id] = owner_position_counters.get(owner_id, 0) + 1
        owner_positions[project_id] = owner_position_counters[owner_id]

    for position, (project_id, _entry) in enumerate(global_order, start=1):
        result[project_id] = ProjectQueueInfo(
            queue_state="queued",
            queue_position=position,
            owner_queue_position=owner_positions[project_id],
        )

    return result


async def project_queue_info(db: AsyncSession, project_id: UUID) -> ProjectQueueInfo | None:
    """Convenience wrapper: one project's queue info, or `None` if it has
    nothing in flight on the GPU bucket right now (no PENDING/RUNNING row
    on any of the three legs).

    Computes the full `compute_queue(db)` result and looks up just this
    project — not micro-optimized to filter server-side for a single
    project, because the same tiny-row-count reasoning in the module
    docstring applies equally here, and keeping one code path (rather
    than a parallel single-project query) avoids the two ever drifting
    apart on ranking logic.
    """
    queue = await compute_queue(db)
    return queue.get(project_id)


__all__ = ["ProjectQueueInfo", "compute_queue", "project_queue_info"]
