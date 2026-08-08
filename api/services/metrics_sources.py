"""Scrape-time metrics readers — pure derivation, zero in-worker state.

WHY scrape-time derivation and not in-worker instrumentation: the Celery
worker is started with `worker_max_tasks_per_child=1` (see
`workers/celery_app.py`), which recycles the worker process after every
single task. Any counter/gauge accumulated in-process (a global, a
`prometheus_client` `Counter().inc()` call, anything living in module state)
would be wiped clean before the next task even starts, so it could never
report anything meaningful — the exact same reasoning
`api/services/circuit_breaker.py`'s module docstring gives for why breaker
state has to live in Redis rather than in-process. The fix here is the same
shape: don't accumulate anything anywhere in the worker. Instead, every
reader in this module re-derives its numbers at scrape time straight from
the systems that already durably hold that state — Postgres rows (job
status/timestamps, usage events) and the Celery broker's Redis lists (queue
depth) — so a scrape five seconds after a worker process recycled sees
exactly the same truth as a scrape five hours later would.

HARD RULE: this module must NEVER `import prometheus_client` and must never
add any in-worker instrumentation. It is scrape-time-readers-only, which is
what lets it be built and tested independently of (and concurrently with)
the metrics-core task that wires these readers into an actual
`prometheus_client` exporter. `test_metrics_sources.py` enforces this with
an AST import scan, not just a comment.

Every reader below is fail-soft: on any exception it logs a warning and
returns an empty/zero-equivalent result rather than raising. A monitoring
endpoint that can 500 the whole API because Redis hiccuped, or because a
Postgres connection briefly dropped, has turned an observability feature
into a new production outage vector — the opposite of its job. This mirrors
`circuit_breaker.py`'s "fail open" stance and `quota.py`'s DB-as-truth
philosophy, applied to reads instead of writes.

Deliberately NO per-project/per-actor grouping anywhere in this module —
that's a cardinality bomb for a metrics backend (one label series per
project forever) and nothing downstream of this module needs it; every
reader here is a small, fixed-cardinality summary.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

from redis.asyncio import from_url
from sqlalchemy import func, select

from api.core.config import get_settings
from api.core.database import AsyncSessionLocal
from api.core.redis_client import get_redis_client
from api.models.dataset import Dataset
from api.models.evaluation_run import EvaluationRun
from api.models.model_artifact import ModelArtifact
from api.models.training_job import TrainingJob
from api.models.usage_event import UsageEvent
from api.schemas.enums import JobStatus
from api.services import circuit_breaker

log = logging.getLogger("api.metrics_sources")

# Celery queue names in use (see `workers/celery_app.py`: `task_default_queue
# = "gpu"`, `task_routes = {"sdg.*": {"queue": "cpu"}}`). A Celery queue is
# just a Redis list keyed by this name on the broker db — `LLEN <name>` is
# the pending-message count for that queue.
QUEUES = ("gpu", "cpu")

# The four job-bearing entity types this module reports on, and the
# JobStatus-bearing column each maps to. Kept as a single tuple so
# `job_counts()` can zero-fill every (type, status) pair without repeating
# the type name list in two places.
_JOB_TYPES = ("dataset", "training", "evaluation", "export")

# Trailing window `job_durations()` looks back over, and an explicit cap on
# rows scanned per type so a pathological backlog can't turn a scrape into
# an unbounded table scan.
_DURATION_WINDOW_HOURS = 24
_DURATION_ROW_LIMIT = 5000

# BreakerState string -> the small integer code metrics need (gauges can't
# carry a string value). Order matches the module docstring's own
# 0=closed/1=half_open/2=open contract.
_BREAKER_STATE_CODES: dict[str, int] = {"closed": 0, "half_open": 1, "open": 2}


async def queue_depths() -> dict[str, int]:
    """`LLEN` per Celery queue, read from the **broker** Redis db.

    Deliberately `settings.celery_broker_url`, NOT `settings.redis_url` —
    the broker (where Celery actually enqueues task messages as Redis list
    entries) lives on a different Redis logical db (`.../1`) than the one
    `api/core/redis_client.py` points at (`.../0`, used for pub/sub job
    progress and the circuit breaker). Reading `redis_url` here would query
    an empty db and silently report zero depth forever.
    """
    settings = get_settings()
    try:
        client = from_url(settings.celery_broker_url, decode_responses=True)
        try:
            return {queue: int(await client.llen(queue)) for queue in QUEUES}
        finally:
            await client.aclose()
    except Exception:
        log.warning("metrics_sources.queue_depths failed, degrading to empty", exc_info=True)
        return {}


async def job_counts() -> dict[tuple[str, str], int]:
    """Grouped `COUNT(*)` per (type, status), covering every `JobStatus`
    member for every type even when its count is zero.

    Zero-filling matters: a metrics series that only appears once its count
    goes non-zero looks, to anyone building a dashboard/alert off it, like
    it silently vanished during the zero stretch rather than legitimately
    being zero the whole time.

    Types map to columns as: Dataset.status ("dataset"), TrainingJob.status
    ("training"), EvaluationRun.status ("evaluation"), and
    ModelArtifact.export_status ("export") — the latter is nullable (null =
    no export ever requested for that artifact, see `quota.py`'s docstring),
    so only non-null rows are counted, exactly like the quota gate does.
    """
    try:
        counts: dict[tuple[str, str], int] = {
            (job_type, status.value): 0 for job_type in _JOB_TYPES for status in JobStatus
        }
        async with AsyncSessionLocal() as session:
            for job_type, status_col in (
                ("dataset", Dataset.status),
                ("training", TrainingJob.status),
                ("evaluation", EvaluationRun.status),
            ):
                stmt = select(status_col, func.count()).group_by(status_col)
                for status, count in (await session.execute(stmt)).all():
                    counts[(job_type, status.value)] = int(count)

            export_stmt = (
                select(ModelArtifact.export_status, func.count())
                .where(ModelArtifact.export_status.is_not(None))
                .group_by(ModelArtifact.export_status)
            )
            for status, count in (await session.execute(export_stmt)).all():
                counts[("export", status.value)] = int(count)

        return counts
    except Exception:
        log.warning("metrics_sources.job_counts failed, degrading to empty", exc_info=True)
        return {}


def _duration_stats(pairs: list[tuple[datetime | None, datetime | None]]) -> tuple[float, float] | None:
    """(avg_seconds, max_seconds) over valid (start, end) pairs, or `None`
    if none were usable. Computed in plain Python, never `EXTRACT(EPOCH...)`
    — the sqlite engine the unit tests run against can't evaluate that
    function, and this way the same code path runs identically against
    Postgres in production and sqlite in tests."""
    deltas = [
        (end - start).total_seconds()
        for start, end in pairs
        if start is not None and end is not None and end >= start
    ]
    if not deltas:
        return None
    return sum(deltas) / len(deltas), max(deltas)


async def job_durations() -> dict[str, tuple[float, float]]:
    """(avg_seconds, max_seconds) per type, over rows whose status flipped
    to COMPLETED within the trailing 24h window (`updated_at >= cutoff`).

    Start timestamp is `started_at` where the model has that column
    (TrainingJob, EvaluationRun — falling back to `created_at` for any row
    where `started_at` is still null, e.g. an old row predating the
    column), and plain `created_at` for models that don't have a
    `started_at` at all (Dataset, ModelArtifact). End timestamp is always
    `updated_at`, since that's the column every one of these models bumps
    on the write that flips status to COMPLETED.

    A type is simply absent from the returned dict if it had no usable
    COMPLETED rows in the window — unlike `job_counts()`, there is no
    meaningful "zero duration" to report, so there's nothing to zero-fill.
    """
    try:
        cutoff = datetime.now(UTC) - timedelta(hours=_DURATION_WINDOW_HOURS)
        durations: dict[str, tuple[float, float]] = {}

        async with AsyncSessionLocal() as session:
            dataset_stmt = (
                select(Dataset.created_at, Dataset.updated_at)
                .where(Dataset.status == JobStatus.COMPLETED, Dataset.updated_at >= cutoff)
                .limit(_DURATION_ROW_LIMIT)
            )
            stats = _duration_stats(list((await session.execute(dataset_stmt)).all()))
            if stats is not None:
                durations["dataset"] = stats

            training_stmt = (
                select(TrainingJob.started_at, TrainingJob.created_at, TrainingJob.updated_at)
                .where(TrainingJob.status == JobStatus.COMPLETED, TrainingJob.updated_at >= cutoff)
                .limit(_DURATION_ROW_LIMIT)
            )
            training_pairs = [
                (started or created, updated)
                for started, created, updated in (await session.execute(training_stmt)).all()
            ]
            stats = _duration_stats(training_pairs)
            if stats is not None:
                durations["training"] = stats

            evaluation_stmt = (
                select(EvaluationRun.started_at, EvaluationRun.created_at, EvaluationRun.updated_at)
                .where(EvaluationRun.status == JobStatus.COMPLETED, EvaluationRun.updated_at >= cutoff)
                .limit(_DURATION_ROW_LIMIT)
            )
            evaluation_pairs = [
                (started or created, updated)
                for started, created, updated in (await session.execute(evaluation_stmt)).all()
            ]
            stats = _duration_stats(evaluation_pairs)
            if stats is not None:
                durations["evaluation"] = stats

            export_stmt = (
                select(ModelArtifact.created_at, ModelArtifact.updated_at)
                .where(
                    ModelArtifact.export_status == JobStatus.COMPLETED,
                    ModelArtifact.updated_at >= cutoff,
                )
                .limit(_DURATION_ROW_LIMIT)
            )
            stats = _duration_stats(list((await session.execute(export_stmt)).all()))
            if stats is not None:
                durations["export"] = stats

        return durations
    except Exception:
        log.warning("metrics_sources.job_durations failed, degrading to empty", exc_info=True)
        return {}


async def breaker_state() -> int:
    """0=closed 1=half_open 2=open — the OpenRouter circuit breaker's
    current state, as an int gauges can carry.

    Reuses `circuit_breaker._OPENED_AT_KEY` and `circuit_breaker._interpret`
    directly rather than re-reading the key name and re-deriving
    closed/open/half_open here: that policy (consecutive-failure threshold,
    open-window elapsed-time math) lives in exactly one place on purpose
    (see that module's docstring), and a second hand-rolled copy of it in a
    metrics reader is exactly how the two would quietly drift apart the
    next time the policy changes. Uses the async Redis surface
    (`api.core.redis_client.get_redis_client`, `settings.redis_url`) — the
    same db `circuit_breaker.assert_closed()` reads — not the broker db
    `queue_depths()` above uses; breaker state and queue depth are two
    different Redis logical dbs entirely.
    """
    settings = get_settings()
    redis = get_redis_client()
    try:
        opened_at = await redis.get(circuit_breaker._OPENED_AT_KEY)
    except Exception:
        log.warning("metrics_sources.breaker_state failed, degrading to closed(0)", exc_info=True)
        return _BREAKER_STATE_CODES["closed"]
    finally:
        await redis.aclose()

    state, _retry_after = circuit_breaker._interpret(
        opened_at, time.time(), settings.openrouter_breaker_open_seconds
    )
    return _BREAKER_STATE_CODES[state]


async def usage_totals() -> dict[tuple[str, str, str], dict[str, float | int]]:
    """SUM(cost_usd)/SUM(prompt_tokens)/SUM(completion_tokens) from
    `usage_events`, grouped by (model, stage, outcome).

    `cost_usd` is nullable per-row (see `UsageEvent`: null means "model not
    in the pricing map", never coerced to 0 there because 0 would silently
    understate spend) — `SUM()` already skips nulls on its own, and any
    group whose every row is null-cost simply reports `cost_usd: 0.0` here,
    which is the correct total for that group, not a lie about any single
    row's price.
    """
    try:
        async with AsyncSessionLocal() as session:
            stmt = select(
                UsageEvent.model,
                UsageEvent.stage,
                UsageEvent.outcome,
                func.sum(UsageEvent.cost_usd),
                func.sum(UsageEvent.prompt_tokens),
                func.sum(UsageEvent.completion_tokens),
            ).group_by(UsageEvent.model, UsageEvent.stage, UsageEvent.outcome)
            rows = (await session.execute(stmt)).all()

        totals: dict[tuple[str, str, str], dict[str, float | int]] = {}
        for model, stage, outcome, cost_sum, prompt_sum, completion_sum in rows:
            totals[(model, stage, outcome)] = {
                "cost_usd": float(cost_sum) if cost_sum is not None else 0.0,
                "prompt_tokens": int(prompt_sum or 0),
                "completion_tokens": int(completion_sum or 0),
            }
        return totals
    except Exception:
        log.warning("metrics_sources.usage_totals failed, degrading to empty", exc_info=True)
        return {}


__all__ = [
    "QUEUES",
    "breaker_state",
    "job_counts",
    "job_durations",
    "queue_depths",
    "usage_totals",
]
