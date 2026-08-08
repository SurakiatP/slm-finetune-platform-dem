"""Scrape-time refresh adapter: `metrics_sources` + `readiness` -> the gauges
declared in `api/core/metrics.py` (M5 "metrics-export-adapter").

WHY this is a separate module rather than folded into `metrics_sources.py` or
`api/core/metrics.py`: `metrics_sources.py` is contractually forbidden from
importing `prometheus_client` at all (see its own module docstring and the
AST-scan test that enforces it) so it can be built, imported, and tested
independently of — and concurrently with — the actual exporter wiring. This
module is the other half of that seam: it is the *only* place that imports
both a `metrics_sources`/`readiness` reader and a `prometheus_client` gauge
in the same file, translating "plain dict of numbers, re-derived fresh from
Postgres/Redis/Celery at scrape time" into "gauge state observed by the
next `render()` call".

`readiness.probe_worker_queues()` is reused as-is (not a second hand-rolled
Celery `inspect()` call) so `slm_worker_up{queue=...}` and `/ready`'s
`worker_gpu`/`worker_cpu` checks can never quietly disagree about whether a
queue has a live consumer — see that function's docstring, which already
anticipates this exact reuse ("a metrics adapter reuses this").
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from api.core.metrics import (
    slm_job_duration_seconds_avg,
    slm_job_duration_seconds_max,
    slm_jobs,
    slm_openrouter_breaker_state,
    slm_openrouter_completion_tokens_total,
    slm_openrouter_cost_usd_total,
    slm_openrouter_prompt_tokens_total,
    slm_queue_depth,
    slm_worker_up,
)
from api.services import metrics_sources, readiness

log = logging.getLogger("api.metrics_export")

# Wall-clock budget for one refresh() cycle, held well under the ~30s scrape
# interval this is meant to run on: a hung dependency (a wedged Redis
# connection, a Postgres query stuck behind a lock) must bound how long a
# scrape can take, not turn into a scrape that never returns.
REFRESH_TIMEOUT_SECONDS = 10.0

# Sentinel distinguishing "this source raised, we have no number" from any
# legitimate value a source could return (including 0, which breaker_state
# uses for "closed") — used only for breaker_state, the one source below
# that isn't a dict, so `_result_or_default` can tell "skip, don't touch the
# gauge" apart from "value is genuinely zero".
_MISSING = object()


async def _gather_sources() -> tuple[Any, ...]:
    """Run every reader concurrently. Each of `metrics_sources`'s functions
    and `readiness.probe_worker_queues` is already independently fail-soft
    (they catch their own exceptions and degrade to an empty/zero result —
    see their docstrings), but `return_exceptions=True` is a second,
    belt-and-braces layer: if one of them ever raised something unexpected
    despite that contract (a bug, a monkeypatched test double), it becomes
    one bad slot in this tuple rather than an exception that cancels its
    siblings mid-flight and poisons the whole gather.
    """
    return await asyncio.gather(
        metrics_sources.queue_depths(),
        metrics_sources.job_counts(),
        metrics_sources.job_durations(),
        metrics_sources.breaker_state(),
        metrics_sources.usage_totals(),
        readiness.probe_worker_queues(),
        return_exceptions=True,
    )


def _result_or_default(result: Any, default: Any) -> Any:
    """Turn one `gather()` slot into a usable value, logging and degrading
    to `default` if that source came back as an exception instead of data."""
    if isinstance(result, BaseException):
        log.warning("metrics_export.refresh: a source failed, degrading", exc_info=result)
        return default
    return result


def _apply_safe(label: str, fn, *args: Any) -> None:
    """Run one `_apply_*` gauge-write step in isolation.

    Mirrors `_result_or_default` above but for the write side: a malformed
    value from one source (e.g. an unexpected shape from a test double, or a
    future bug) must not stop the *other* gauges in this same refresh() from
    being updated with the good data they already have.
    """
    try:
        fn(*args)
    except Exception:
        log.warning("metrics_export.refresh: applying %s failed", label, exc_info=True)


def _apply_queue_depths(depths: dict[str, int]) -> None:
    # STALE-SERIES handling: clear the whole labelled family before
    # repopulating it, so a queue that stopped being reported (renamed,
    # decommissioned) doesn't freeze forever at its last observed value —
    # `queue_depths()` degrading to `{}` on a Redis outage would otherwise
    # leave the previous depth looking permanently current. The registry is
    # module-global, so this clear-then-set happens within one synchronous
    # stretch of this refresh(); a scrape landing between the two would see
    # a briefly empty family, which is acceptable at a 30s scrape interval.
    slm_queue_depth.clear()
    for queue, depth in depths.items():
        slm_queue_depth.labels(queue=queue).set(depth)


def _apply_worker_up(served: dict[str, bool]) -> None:
    # Same stale-series reasoning as `_apply_queue_depths` — see its comment.
    slm_worker_up.clear()
    for queue, up in served.items():
        slm_worker_up.labels(queue=queue).set(1.0 if up else 0.0)


def _apply_job_counts(counts: dict[tuple[str, str], int]) -> None:
    # Same stale-series reasoning as `_apply_queue_depths` — see its comment.
    # `job_counts()` already zero-fills every (type, status) pair on success,
    # so clearing first only matters on the fail-soft-to-`{}` path.
    slm_jobs.clear()
    for (job_type, outcome), count in counts.items():
        slm_jobs.labels(type=job_type, outcome=outcome).set(count)


def _apply_job_durations(durations: dict[str, tuple[float, float]]) -> None:
    # Same stale-series reasoning as `_apply_queue_depths` — see its comment.
    slm_job_duration_seconds_avg.clear()
    slm_job_duration_seconds_max.clear()
    for job_type, (avg_seconds, max_seconds) in durations.items():
        slm_job_duration_seconds_avg.labels(type=job_type).set(avg_seconds)
        slm_job_duration_seconds_max.labels(type=job_type).set(max_seconds)


def _apply_breaker_state(state: Any) -> None:
    # Unlabelled gauge — no `.clear()` concept applies (there is exactly one
    # series). If the source failed, `state` is `_MISSING` and we deliberately
    # leave the gauge at its last known value rather than guessing a state
    # (e.g. forcing "closed") we don't actually know to be true.
    if state is _MISSING:
        return
    slm_openrouter_breaker_state.set(state)


def _apply_usage_totals(totals: dict[tuple[str, str, str], dict[str, float | int]]) -> None:
    # Same stale-series reasoning as `_apply_queue_depths` — see its comment.
    # All three OpenRouter usage gauges share the same (model, stage, outcome)
    # label set and the same source, so they're cleared and repopulated together.
    slm_openrouter_cost_usd_total.clear()
    slm_openrouter_prompt_tokens_total.clear()
    slm_openrouter_completion_tokens_total.clear()
    for (model, stage, outcome), values in totals.items():
        slm_openrouter_cost_usd_total.labels(model=model, stage=stage, outcome=outcome).set(
            values["cost_usd"]
        )
        slm_openrouter_prompt_tokens_total.labels(model=model, stage=stage, outcome=outcome).set(
            values["prompt_tokens"]
        )
        slm_openrouter_completion_tokens_total.labels(
            model=model, stage=stage, outcome=outcome
        ).set(values["completion_tokens"])


async def refresh() -> None:
    """Re-derive every scrape-time gauge from `metrics_sources` + `readiness`.

    Must NEVER raise: this sits directly in (or just ahead of) the `/metrics`
    scrape path, and a broken dependency turning an observability endpoint
    into a 500 would be strictly worse than serving a briefly stale or
    partially-empty scrape — the same "fail open" stance `metrics_sources.py`
    and `readiness.py` already take one layer down. This outermost
    try/except is the last line of defense on top of those inner fail-soft
    layers, `return_exceptions=True` in `_gather_sources`, and the per-gauge
    isolation in `_apply_safe`.
    """
    try:
        results = await asyncio.wait_for(_gather_sources(), timeout=REFRESH_TIMEOUT_SECONDS)
    except Exception:
        # Covers both `TimeoutError` (a source hung past REFRESH_TIMEOUT_SECONDS)
        # and any exception `_gather_sources` itself couldn't already isolate
        # into one slot — either way, skip this cycle entirely and let the
        # next scheduled refresh try again with fresh state.
        log.warning(
            "metrics_export.refresh: gather timed out or failed, skipping this cycle",
            exc_info=True,
        )
        return

    queue_depths = _result_or_default(results[0], {})
    job_counts = _result_or_default(results[1], {})
    job_durations = _result_or_default(results[2], {})
    breaker_state = _result_or_default(results[3], _MISSING)
    usage_totals = _result_or_default(results[4], {})
    worker_queues = _result_or_default(results[5], {})

    _apply_safe("queue_depths", _apply_queue_depths, queue_depths)
    _apply_safe("worker_up", _apply_worker_up, worker_queues)
    _apply_safe("job_counts", _apply_job_counts, job_counts)
    _apply_safe("job_durations", _apply_job_durations, job_durations)
    _apply_safe("breaker_state", _apply_breaker_state, breaker_state)
    _apply_safe("usage_totals", _apply_usage_totals, usage_totals)


__all__ = ["REFRESH_TIMEOUT_SECONDS", "refresh"]
