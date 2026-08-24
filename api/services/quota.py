"""Concurrency quota gate — caps how many SDG / GPU jobs can be in flight.

Two buckets:
  - `Bucket.SDG` counts `Dataset.status IN (pending, running)`.
  - `Bucket.GPU` counts `TrainingJob.status`, `EvaluationRun.status`, and
    `ModelArtifact.export_status`, each `IN (pending, running)`, summed
    together as ONE bucket. Training, evaluation, and export all pin the
    same GPU (RTX 3060 12GB, see `require.md`) one job at a time, so they
    have to share a single cap rather than each getting their own —
    otherwise a full training run plus a full eval plus a full export
    could all be "within quota" individually while three-deep on a GPU
    that fits one.

    `ModelArtifact` has no generic `status` field (unlike Dataset /
    TrainingJob / EvaluationRun) — migration 0006 added `export_status`
    specifically for the export pipeline, and completion is otherwise
    inferred from `gguf_uri` / `export_error_message`. `export_status` is
    nullable (null = no export ever requested for that artifact), so the
    `IN (...)` filter alone already excludes nulls; no extra `IS NOT NULL`
    guard is needed.

Why the DATABASE is the counting source of truth, not a Redis counter:
the DB is already the truth for job state, and it self-heals — if a
worker process is SIGKILLed mid-job, the row is left PENDING/RUNNING
forever, but `api/services/job_reconcile.py` sweeps every 5 minutes and
flips orphaned jobs to `failed`, so an in-flight count derived from the DB
drops back down on its own within one sweep interval. A Redis counter
has no equivalent self-healing: it is incremented on submit and
decremented on completion, so it leaks exactly on the SIGKILL-before-
decrement case that commit `b93091e` had to patch up for a different
counter. A leaked Redis counter never comes back down by itself, which
means it 429s every future submission for that actor/bucket forever,
silently, until someone notices and manually resets it — worse than the
problem it was meant to solve. Job volumes on this platform are tiny (a
handful of concurrent jobs at most, per the per-actor/global caps below),
so the extra `SELECT COUNT(*)` per submission this implies is irrelevant
cost.

Why 429 with `Retry-After`, not 503: 503 Service Unavailable is the
signal most HTTP client libraries (and reverse proxies) treat as "safe
to auto-retry, possibly with backoff" — exactly the wrong incentive when
the reason for the rejection *is* an already-saturated queue. 429 Too
Many Requests paired with an explicit `Retry-After` header tells the
caller precisely how long to back off, without implying the server itself
is unhealthy.

Why anonymous callers only trip the global cap, never the per-actor cap:
per-actor scoping is implemented as a join through `Project.owner_id`
(see the join-depth comments on each `_*_actor_count` helper below), and
there is no owner to join on for an anonymous caller — the DB has no IP
address to bucket unauthenticated requests by, and this module
deliberately does not add one. Under today's `AUTH_REQUIRED=false`
setting this means the per-actor limit is inert (every caller looks
anonymous) and only the global cap does any work; that is intentional
and stays true until `AUTH_REQUIRED` flips to `true` project-wide. Do
NOT paper over this with a Redis-per-IP side channel — see the Redis
rationale above for why that trades a self-healing signal for a leaky
one.
"""

from __future__ import annotations

from enum import Enum

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings
from api.models.dataset import Dataset
from api.models.evaluation_run import EvaluationRun
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.enums import JobStatus

# Rows in either of these two states count as "in flight" for quota
# purposes. Deliberately excludes completed/failed/cancelled — those are
# done consuming resources and must not count against anyone.
_IN_FLIGHT = (JobStatus.PENDING, JobStatus.RUNNING)


class Bucket(str, Enum):
    SDG = "sdg"
    GPU = "gpu"


# ---- SDG bucket: Dataset -> Project is a single hop (Dataset.project_id
# is a direct FK to projects.id, per api/models/dataset.py). -----------------


async def _sdg_global_count(db: AsyncSession) -> int:
    stmt = select(func.count()).select_from(Dataset).where(Dataset.status.in_(_IN_FLIGHT))
    return int((await db.execute(stmt)).scalar_one())


async def _sdg_actor_count(db: AsyncSession, actor_id: str) -> int:
    stmt = (
        select(func.count())
        .select_from(Dataset)
        .join(Project, Project.id == Dataset.project_id)
        .where(Dataset.status.in_(_IN_FLIGHT), Project.owner_id == actor_id)
    )
    return int((await db.execute(stmt)).scalar_one())


# ---- GPU bucket: three tables, three different join depths to Project. ----
#
#   TrainingJob   -> Project                                  (1 hop)
#     TrainingJob.project_id is a direct FK to projects.id.
#   ModelArtifact -> TrainingJob -> Project                    (2 hops)
#     ModelArtifact.training_job_id -> training_jobs.id, then
#     TrainingJob.project_id -> projects.id.
#   EvaluationRun -> ModelArtifact -> TrainingJob -> Project    (3 hops)
#     EvaluationRun.model_artifact_id -> model_artifacts.id, then the two
#     hops above. EvaluationRun has no project_id of its own — it only
#     knows its model_artifact and its (evaluation) dataset.
#
# These depths mirror api/services/job_ownership.py's `resolve_job_owner`,
# which walks the identical FKs for a different purpose (WebSocket auth)
# and documents the same chain — verified independently against the FK
# declarations in api/models/{dataset,training_job,evaluation_run,
# model_artifact,project}.py rather than trusted from that module.


async def _gpu_global_count(db: AsyncSession) -> int:
    training_stmt = (
        select(func.count()).select_from(TrainingJob).where(TrainingJob.status.in_(_IN_FLIGHT))
    )
    eval_stmt = (
        select(func.count()).select_from(EvaluationRun).where(EvaluationRun.status.in_(_IN_FLIGHT))
    )
    export_stmt = (
        select(func.count())
        .select_from(ModelArtifact)
        .where(ModelArtifact.export_status.in_(_IN_FLIGHT))
    )
    training = int((await db.execute(training_stmt)).scalar_one())
    evaluation = int((await db.execute(eval_stmt)).scalar_one())
    export = int((await db.execute(export_stmt)).scalar_one())
    return training + evaluation + export


async def _gpu_actor_count(db: AsyncSession, actor_id: str) -> int:
    # Outer joins, not inner: `TrainingJob.project_id` is nullable (orphaned
    # runs whose Project was deleted — migration 0012_training_decouple, see
    # api/services/ownership.py's orphan-policy section). An INNER JOIN would
    # silently drop an orphan's row from this per-actor count entirely,
    # rather than surfacing it with `Project.owner_id` NULL the way the rest
    # of the codebase reasons about orphans. It doesn't change the RESULT
    # here — an orphan has no owner to match `actor_id`, so it's excluded
    # from every per-actor bucket either way (fail-closed: an orphan cannot
    # be attributed to any actor) — but it keeps that exclusion happening by
    # construction (NULL != actor_id) rather than by the join silently
    # dropping the row, matching `ownership.py`'s `scope_trainings_to_owner`
    # et al. Global counts (`_gpu_global_count` above) don't join Project at
    # all, so orphans always count there regardless.
    training_stmt = (
        select(func.count())
        .select_from(TrainingJob)
        .outerjoin(Project, Project.id == TrainingJob.project_id)
        .where(TrainingJob.status.in_(_IN_FLIGHT), Project.owner_id == actor_id)
    )
    export_stmt = (
        select(func.count())
        .select_from(ModelArtifact)
        .join(TrainingJob, TrainingJob.id == ModelArtifact.training_job_id)
        .outerjoin(Project, Project.id == TrainingJob.project_id)
        .where(ModelArtifact.export_status.in_(_IN_FLIGHT), Project.owner_id == actor_id)
    )
    eval_stmt = (
        select(func.count())
        .select_from(EvaluationRun)
        .join(ModelArtifact, ModelArtifact.id == EvaluationRun.model_artifact_id)
        .join(TrainingJob, TrainingJob.id == ModelArtifact.training_job_id)
        .outerjoin(Project, Project.id == TrainingJob.project_id)
        .where(EvaluationRun.status.in_(_IN_FLIGHT), Project.owner_id == actor_id)
    )
    training = int((await db.execute(training_stmt)).scalar_one())
    export = int((await db.execute(export_stmt)).scalar_one())
    evaluation = int((await db.execute(eval_stmt)).scalar_one())
    return training + export + evaluation


async def assert_can_submit(db: AsyncSession, *, bucket: Bucket, actor_id: str | None) -> None:
    """Raise 429 if submitting one more `bucket` job would exceed quota.

    Global cap is always enforced, regardless of `actor_id`. Per-actor cap
    is only enforced when `actor_id is not None` — an anonymous caller has
    no `Project.owner_id` to join on (see module docstring) and falls
    under the global cap alone.

    Takes a plain `actor_id: str | None` rather than a `CurrentUser`
    object on purpose: the submit services this gate is meant to be
    called from key off `api.core.request_context.current_user_id()`,
    which already resolves to exactly this shape. Accepting a `CurrentUser`
    here would require importing `api.core.auth` (or
    `api.services.ownership`, which imports it) at module scope, and that
    module does `import jwt` at module scope — the GPU worker image ships
    no PyJWT, so that import crash-loops any worker process that reaches
    it. This module needs no such import at all: the string id is enough
    to build the `Project.owner_id == actor_id` filters above.
    """
    settings = get_settings()

    if bucket is Bucket.SDG:
        global_limit = settings.quota_max_sdg_jobs_global
        actor_limit = settings.quota_max_sdg_jobs_per_actor
        global_count = await _sdg_global_count(db)
        actor_count_fn = _sdg_actor_count
    else:
        global_limit = settings.quota_max_gpu_jobs_global
        actor_limit = settings.quota_max_gpu_jobs_per_actor
        global_count = await _gpu_global_count(db)
        actor_count_fn = _gpu_actor_count

    if global_count >= global_limit:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Global {bucket.value} job quota reached "
                f"({global_count}/{global_limit} in flight). Try again shortly."
            ),
            headers={"Retry-After": str(settings.quota_retry_after_seconds)},
        )

    if actor_id is not None:
        actor_count = await actor_count_fn(db, actor_id)
        if actor_count >= actor_limit:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Your {bucket.value} job quota reached "
                    f"({actor_count}/{actor_limit} in flight). Try again shortly."
                ),
                headers={"Retry-After": str(settings.quota_retry_after_seconds)},
            )


__all__ = ["Bucket", "assert_can_submit"]
