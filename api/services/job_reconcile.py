"""Reconcile jobs whose worker vanished, so no client waits forever.

Every long-running job here is watched over `/ws/jobs/{job_id}`, and the
contract that stream promises is that a job always ends with a **terminal
frame** — `JobCompleted` or `JobFailed`. A worker that dies mid-task breaks
that promise in the worst possible way: the row stays `running` forever, no
frame is ever published, and `smart-model-tune`'s `useTrainingWebSocket`
(the only WebSocket consumer it wires) sits on the last progress frame it
saw with no way to learn the job is gone. `BACKEND_GAP_ANALYSIS.md`'s P1
acceptance criterion — "งานจบด้วยสถานะที่ถูกต้องแม้ worker restart" — is
exactly this case, and nothing implemented it.

This module sweeps those orphans and gives them the ending they never got.

## What counts as an orphan

**Both** conditions must hold, and the conjunction is the whole design:

1. the row's Celery task id is absent from `inspect()`'s active + reserved +
   scheduled sets, and
2. the job has been silent longer than `settings.job_orphan_grace_minutes`.

Either test alone is unsafe. Condition 1 alone kills healthy jobs whenever
the broker hiccups or a worker is slow to answer. Condition 2 alone kills a
training that is legitimately quiet — `task_time_limit` is three hours.

## Why the liveness signal is the Redis snapshot, not `updated_at`

This is the subtle part, and getting it wrong is worse than not sweeping at
all. **No worker writes its DB row while it runs.** `workers/tasks/training.py`
stamps `mlflow_run_id` at :117 and then touches nothing until the terminal
block at :231; `data_generation.py` writes only at the end. So `updated_at`
is frozen for the entire duration of a job, and a healthy two-hour training
looks two hours stale from the first minute.

What *does* move is `job:{task_id}:last`, rewritten by
`workers/progress.py::publish_ws_message` on every published frame. Its
`timestamp` field is therefore the real liveness signal. `updated_at` is used
only as a fallback for a job that never published a frame at all (e.g. one
that died before its first progress callback), where it is the best evidence
available.

## What an orphan gets

Status flipped to `failed` with an error naming the cause, an
`audit_events` row, and — mirroring `workers/progress.py`'s ordering — the
snapshot key SET **before** the channel PUBLISH, so a client subscribing in
between reads a populated snapshot rather than an empty one. The frame is a
normal `JobFailed` carrying `error_type="OrphanedJob"`; clients need no new
handling for it, which is the point.

Deliberately uses the **async** Redis client from `api/core/redis_client.py`
rather than importing `workers/progress.py` — that module's client is sync
and belongs to the worker process.

Safe to run concurrently with itself (startup pass overlapping the loop, or
two API replicas): every flip re-checks the status is still non-terminal
inside the transaction before writing.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings
from api.core.database import AsyncSessionLocal
from api.core.redis_client import (
    JOB_SNAPSHOT_TTL_SECONDS,
    get_redis_client,
    job_channel,
    job_snapshot_key,
)
from api.models.dataset import Dataset
from api.models.evaluation_run import EvaluationRun
from api.models.model_artifact import ModelArtifact
from api.models.training_job import TrainingJob
from api.schemas.enums import JobStatus
from api.schemas.progress import JobFailed
from api.services import audit_service

log = logging.getLogger(__name__)

# Statuses a sweep may act on. Both are included: a job whose enqueue was
# lost by the broker sits at `pending` forever and looks identical to a dead
# `running` one from the user's side — the UI spins either way.
_SWEEPABLE = (JobStatus.PENDING, JobStatus.RUNNING)

# Anything at or past these needs no rescue.
_TERMINAL = (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)

_ERROR_TYPE = "OrphanedJob"

# How long to wait for workers to answer an `inspect()` broadcast.
_INSPECT_TIMEOUT_SECONDS = 2.0

# How long `run_forever` waits before its first sweep. Not zero: on a cold
# boot the worker fleet may not have registered with the broker yet, and a
# sweep that runs before they do would see an empty active set. The
# abort-on-unreachable guard covers the total-outage case, but this covers
# the narrower "workers are coming up right now" race, and it keeps a
# short-lived process (a test client, a `--reload` cycle) from paying for a
# broker round-trip it will never use.
_INITIAL_DELAY_SECONDS = 15


@dataclass(frozen=True, slots=True)
class _Target:
    """One job-bearing table and the column names that differ across them.

    `ModelArtifact` is the odd one out on every axis — its status column is
    `export_status`, its error column is `export_error_message`, its task id
    is `export_celery_task_id`, and it has no `ended_at` at all. Encoding
    that here keeps the sweep loop free of per-model branching.
    """

    model: type
    status_attr: str
    task_id_attr: str
    error_attr: str
    resource_type: str
    has_ended_at: bool


_TARGETS: tuple[_Target, ...] = (
    _Target(Dataset, "status", "celery_task_id", "error_message", "dataset", False),
    _Target(TrainingJob, "status", "celery_task_id", "error_message", "training", True),
    _Target(
        EvaluationRun, "status", "celery_task_id", "error_message", "evaluation", True
    ),
    _Target(
        ModelArtifact,
        "export_status",
        "export_celery_task_id",
        "export_error_message",
        "model_export",
        False,
    ),
)


@dataclass
class ReconcileReport:
    """Outcome of one pass — returned so the caller can log or test it."""

    aborted: bool = False
    abort_reason: str | None = None
    checked: int = 0
    reconciled: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.reconciled)


def _broker_reachable(app: Any) -> bool:
    """Can we talk to the broker at all?

    This is what separates "no worker is online" from "we cannot see
    anything" — `inspect()` returns None for both, and they call for opposite
    responses. A live broker with zero workers answering is a *fact*: no
    process can be executing the task, so a job silent past the grace period
    is genuinely orphaned. A dead broker is ignorance: workers may be running
    fine on the other side of it, and failing their jobs would be the
    destructive guess this module exists to avoid.
    """
    try:
        conn = app.connection()
        try:
            conn.ensure_connection(max_retries=0, timeout=_INSPECT_TIMEOUT_SECONDS)
        finally:
            conn.release()
        return True
    except Exception:  # noqa: BLE001 — any failure here means "cannot confirm"
        log.warning("broker unreachable; reconcile cannot distinguish orphans", exc_info=True)
        return False


def _active_task_ids(inspector: Any, app: Any = None) -> set[str] | None:
    """Every task id Celery currently knows about, or None if it can't say.

    None is NOT an empty set. Treating "cannot say" as "nothing is running"
    would fail every job on the box on the next pass, so the caller aborts on
    None and this function never guesses.

    But silence from `inspect()` has two causes, and only one of them is
    ignorance. **Zero workers online** is the single most likely reason a job
    is orphaned in the first place — this deployment runs one worker container
    at `--concurrency=1`, so "the worker died" and "nobody answers inspect()"
    are the same event. Aborting on it made the feature refuse to act in
    exactly the scenario it was built for (observed on the vast.ai box: the
    row sat in `running` while the sweep logged "broker or workers
    unreachable" every 5 minutes). So when all three probes come back empty we
    ask the broker directly: reachable means "no workers, and that is a fact"
    → empty set, and the grace period does the rest; unreachable means
    ignorance → None.

    Synchronous and blocking: each probe is a broadcast that waits for the
    workers to reply, and waits out its full timeout when the broker is
    unreachable. Callers must run this off the event loop — see
    `reconcile_once`.
    """
    try:
        active = inspector.active()
        reserved = inspector.reserved()
        scheduled = inspector.scheduled()
    except Exception:  # noqa: BLE001 — a broker error must not raise into the loop
        log.warning("celery inspect() failed; skipping this reconcile pass", exc_info=True)
        return None

    if active is None and reserved is None and scheduled is None:
        if app is not None and _broker_reachable(app):
            log.info("no celery workers are online; treating the fleet as empty")
            return set()
        return None

    ids: set[str] = set()
    for bucket in (active, reserved):
        for entries in (bucket or {}).values():
            for entry in entries or []:
                task_id = entry.get("id")
                if task_id:
                    ids.add(task_id)
    # `scheduled()` nests the task under a "request" key, unlike the other two.
    for entries in (scheduled or {}).values():
        for entry in entries or []:
            task_id = (entry.get("request") or {}).get("id")
            if task_id:
                ids.add(task_id)
    return ids


async def _last_seen(redis: Any, task_id: str) -> datetime | None:
    """When this job last published a frame, from `job:{id}:last`.

    None means no snapshot exists — the caller falls back to `updated_at`.
    """
    try:
        raw = await redis.get(job_snapshot_key(task_id))
    except Exception:  # noqa: BLE001 — Redis down must not fail the sweep
        log.warning("could not read snapshot for job %s", task_id, exc_info=True)
        return None
    if not raw:
        return None
    try:
        ts = json.loads(raw).get("timestamp")
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError, json.JSONDecodeError):
        # A corrupt or legacy frame tells us nothing about liveness; treat it
        # as "no snapshot" rather than as "definitely stale".
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def _resolve_project_id(db: AsyncSession, target: _Target, row: Any):
    """Walk to the owning `Project.id` — 0, 1 or 2 hops depending on table.

    Mirrors the join depths `api/services/job_ownership.py` documents.
    """
    if target.model in (Dataset, TrainingJob):
        return row.project_id
    if target.model is EvaluationRun:
        stmt = (
            select(TrainingJob.project_id)
            .select_from(ModelArtifact)
            .join(TrainingJob, TrainingJob.id == ModelArtifact.training_job_id)
            .where(ModelArtifact.id == row.model_artifact_id)
            .limit(1)
        )
    else:  # ModelArtifact
        stmt = (
            select(TrainingJob.project_id)
            .where(TrainingJob.id == row.training_job_id)
            .limit(1)
        )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _publish_orphan_frame(redis: Any, task_id: str, error: str) -> None:
    """Snapshot then publish, in that order — same as `workers/progress.py`.

    A client that subscribes between the two operations reads a populated
    snapshot instead of an empty one. A failed SET is logged and swallowed:
    losing the snapshot must never cost us the live frame.
    """
    payload = JobFailed(job_id=task_id, error=error, error_type=_ERROR_TYPE).model_dump_json()
    try:
        await redis.set(job_snapshot_key(task_id), payload, ex=JOB_SNAPSHOT_TTL_SECONDS)
    except Exception:  # noqa: BLE001
        log.warning("failed to write orphan snapshot for job %s", task_id, exc_info=True)
    try:
        await redis.publish(job_channel(task_id), payload)
    except Exception:  # noqa: BLE001
        log.warning("failed to publish orphan frame for job %s", task_id, exc_info=True)


async def reconcile_once(
    db: AsyncSession, *, redis: Any = None, inspector: Any = None, app: Any = None
) -> ReconcileReport:
    """Sweep every job-bearing table once.

    `redis`, `inspector` and `app` are injectable so tests need neither a
    broker nor a live Redis; production callers pass none of them.
    """
    settings = get_settings()
    grace = timedelta(minutes=settings.job_orphan_grace_minutes)
    report = ReconcileReport()

    if inspector is None:
        from workers.celery_app import celery_app

        app = app or celery_app
        # Bounded: an unbounded broadcast against a dead broker blocks the
        # worker thread for the default timeout on every pass.
        inspector = celery_app.control.inspect(timeout=_INSPECT_TIMEOUT_SECONDS)
    # An injected inspector with no injected app leaves `app` None, which
    # `_active_task_ids` reads as "cannot probe the broker" and answers
    # conservatively. That keeps tests hermetic — a test that wants the
    # "workers are gone but the broker is fine" branch has to say so.

    # Offloaded to a worker thread: `inspect()`'s three probes are synchronous
    # broadcasts that block until the workers reply — or, when the broker is
    # unreachable, until their timeout expires. Calling them inline would
    # stall the event loop for every other request. Same convention
    # `api/core/auth.py` documents for the blocking JWKS fetch.
    active_ids = await asyncio.to_thread(_active_task_ids, inspector, app)
    if active_ids is None:
        report.aborted = True
        report.abort_reason = "celery inspect() returned nothing and the broker is unreachable"
        log.warning("reconcile aborted: %s", report.abort_reason)
        return report

    owns_redis = redis is None
    redis = redis if redis is not None else get_redis_client()
    now = datetime.now(timezone.utc)

    try:
        for target in _TARGETS:
            status_col = getattr(target.model, target.status_attr)
            task_col = getattr(target.model, target.task_id_attr)
            rows = (
                (
                    await db.execute(
                        select(target.model).where(
                            status_col.in_(_SWEEPABLE), task_col.is_not(None)
                        )
                    )
                )
                .scalars()
                .all()
            )

            for row in rows:
                report.checked += 1
                task_id = getattr(row, target.task_id_attr)

                if task_id in active_ids:
                    continue  # a worker is holding it right now

                last_seen = await _last_seen(redis, task_id)
                source = "snapshot"
                if last_seen is None:
                    last_seen = _as_utc(getattr(row, "updated_at", None))
                    source = "updated_at"
                if last_seen is not None and now - last_seen < grace:
                    continue  # still within the grace window

                error = (
                    f"No worker is executing this job and it has published nothing "
                    f"since {last_seen.isoformat() if last_seen else 'ever'} "
                    f"(via {source}, grace {settings.job_orphan_grace_minutes}m). "
                    f"The worker running Celery task {task_id} most likely died."
                )[:4000]

                # Re-read inside the write to stay safe against a concurrent
                # pass (two replicas, or startup overlapping the loop) and
                # against the worker finishing between our SELECT and here.
                fresh = await db.get(target.model, row.id)
                if fresh is None or getattr(fresh, target.status_attr) in _TERMINAL:
                    continue

                setattr(fresh, target.status_attr, JobStatus.FAILED)
                setattr(fresh, target.error_attr, error)
                if target.has_ended_at:
                    fresh.ended_at = now

                audit_service.record(
                    db,
                    action="job.orphan_reconciled",
                    resource_type=target.resource_type,
                    resource_id=str(fresh.id),
                    project_id=await _resolve_project_id(db, target, fresh),
                    outcome="failure",
                    metadata={
                        "job_id": task_id,
                        "last_seen": last_seen.isoformat() if last_seen else None,
                        "liveness_source": source,
                        "grace_minutes": settings.job_orphan_grace_minutes,
                    },
                )
                await db.commit()

                await _publish_orphan_frame(redis, task_id, error)
                report.reconciled.append(task_id)
                log.warning(
                    "reconciled orphaned %s %s (job %s)",
                    target.resource_type,
                    fresh.id,
                    task_id,
                    extra={"job_id": task_id, "resource_type": target.resource_type},
                )
    finally:
        if owns_redis:
            await redis.aclose()

    if report.count:
        log.info("reconcile pass finished: %d orphan(s) of %d checked", report.count, report.checked)
    return report


async def run_forever(
    interval_seconds: int = 300, initial_delay_seconds: int = _INITIAL_DELAY_SECONDS
) -> None:
    """Sweep shortly after start, then every `interval_seconds`, until cancelled.

    An early first pass matters: a restart is the likeliest moment for
    orphans to exist, because whatever killed the worker often took the API
    with it. `initial_delay_seconds` keeps that from firing before the worker
    fleet has registered with the broker.

    This runs as a task rather than inline in the lifespan because
    `reconcile_once` broadcasts to the workers and waits for replies. Awaiting
    that during startup would delay readiness on every boot and make the API's
    startup depend on the broker's health — precisely the coupling this module
    exists to survive.

    One pass failing must never kill the loop — a broker outage would
    otherwise silently disable reconciliation until the next deploy.
    """
    try:
        await asyncio.sleep(initial_delay_seconds)
        while True:
            try:
                async with AsyncSessionLocal() as session:
                    report = await reconcile_once(session)
                if report.count:
                    log.info("reconcile: %d orphan(s) recovered", report.count)
            except Exception:  # noqa: BLE001 — one bad pass must not end the loop
                log.exception("job reconcile pass failed; continuing")
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        log.info("job reconcile loop cancelled")
        raise


__all__ = ["ReconcileReport", "reconcile_once", "run_forever"]
