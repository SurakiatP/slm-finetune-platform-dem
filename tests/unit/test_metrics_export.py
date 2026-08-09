"""Unit tests for `api/services/metrics_export.py` (M5 "metrics-export-adapter").

Layers covered:
  1. Total-outage resilience — every source (`metrics_sources.*` and
     `readiness.probe_worker_queues`) raising must still let `refresh()`
     return normally and `api.core.metrics.render()` keep producing parseable
     exposition (a scrape must never 500 because of a dependency outage).
  2. Stale-series handling — a label series that stops being reported by a
     source (e.g. a model with no more usage rows) must disappear from
     `render()` after the next `refresh()`, not freeze at its last value.
  3. Per-gauge value mapping — `slm_worker_up`, `slm_queue_depth` (including
     the zero-must-still-export case), and `slm_openrouter_breaker_state` end
     up with the exact values their source reported.
  4. The `asyncio.wait_for` bound — a source that hangs past
     `REFRESH_TIMEOUT_SECONDS` must not make `refresh()` itself hang.

All six sources are monkeypatched in every test via `_patch_all_sources` so
no test ever touches a real Postgres/Redis/Celery connection — the same
"replace at the source-module boundary" approach `test_metrics_sources.py`
itself is exempt from needing (it owns the real readers) but every *caller*
of those readers should use.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from prometheus_client.parser import text_string_to_metric_families

from api.core import metrics as metrics_core
from api.services import metrics_export, metrics_sources, readiness


def _rendered_families(text: str) -> dict[str, Any]:
    """Parse `render()`'s exposition text into {metric_name: Metric}."""
    return {family.name: family for family in text_string_to_metric_families(text)}


def _samples_by_label(family: Any, label: str) -> dict[str, float]:
    return {sample.labels[label]: sample.value for sample in family.samples}


async def _raise(*_args: Any, **_kwargs: Any) -> None:
    raise RuntimeError("source exploded")


def _patch_all_sources(
    monkeypatch: pytest.MonkeyPatch,
    *,
    queue_depths: dict[str, int] | None = None,
    job_counts: dict[tuple[str, str], int] | None = None,
    job_durations: dict[str, tuple[float, float]] | None = None,
    breaker_state: int = 0,
    usage_totals: dict[tuple[str, str, str], dict[str, float | int]] | None = None,
    worker_queues: dict[str, bool] | None = None,
    dependency_up: dict[str, bool] | None = None,
    job_failures: dict[tuple[str, str], int] | None = None,
) -> None:
    """Patch every source `refresh()` depends on to a fast, deterministic
    stub, so each test only has to spell out the source(s) it actually cares
    about instead of every test needing to know about all eight.

    Patches `metrics_sources`/`readiness` attributes directly (not
    `metrics_export`'s names) because `metrics_export.refresh()` calls them
    as `metrics_sources.queue_depths()` / `readiness.probe_worker_queues()`
    — a late-bound module attribute lookup, not an early-bound `from ...
    import queue_depths` — which is exactly what makes them monkeypatchable
    here.
    """

    async def _return(value: Any) -> Any:
        return value

    monkeypatch.setattr(metrics_sources, "queue_depths", lambda: _return(queue_depths or {}))
    monkeypatch.setattr(metrics_sources, "job_counts", lambda: _return(job_counts or {}))
    monkeypatch.setattr(metrics_sources, "job_durations", lambda: _return(job_durations or {}))
    monkeypatch.setattr(metrics_sources, "breaker_state", lambda: _return(breaker_state))
    monkeypatch.setattr(metrics_sources, "usage_totals", lambda: _return(usage_totals or {}))
    monkeypatch.setattr(readiness, "probe_worker_queues", lambda: _return(worker_queues or {}))
    monkeypatch.setattr(readiness, "probe_dependencies", lambda: _return(dependency_up or {}))
    monkeypatch.setattr(
        metrics_sources, "job_failure_counts", lambda: _return(job_failures or {})
    )


# =============================================================================
# 1. Total outage resilience
# =============================================================================


async def test_refresh_survives_every_source_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metrics_sources, "queue_depths", _raise)
    monkeypatch.setattr(metrics_sources, "job_counts", _raise)
    monkeypatch.setattr(metrics_sources, "job_durations", _raise)
    monkeypatch.setattr(metrics_sources, "breaker_state", _raise)
    monkeypatch.setattr(metrics_sources, "usage_totals", _raise)
    monkeypatch.setattr(readiness, "probe_worker_queues", _raise)
    monkeypatch.setattr(readiness, "probe_dependencies", _raise)
    monkeypatch.setattr(metrics_sources, "job_failure_counts", _raise)

    await metrics_export.refresh()  # must not raise

    body, content_type = metrics_core.render()
    assert content_type.startswith("text/plain")
    families = list(text_string_to_metric_families(body.decode("utf-8")))
    # Non-vacuity: the registry still has metrics (HTTP counters etc.) even
    # though every scrape-time source failed — proves render() itself never
    # broke, not just that it returned some (possibly empty) bytes.
    assert len(families) > 0


# =============================================================================
# 2. Stale-series handling
# =============================================================================


async def test_usage_totals_series_disappears_once_source_goes_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_all_sources(
        monkeypatch,
        usage_totals={
            ("X", "sdg", "success"): {"cost_usd": 1.5, "prompt_tokens": 10, "completion_tokens": 20}
        },
    )
    await metrics_export.refresh()
    text = metrics_core.render()[0].decode("utf-8")
    assert 'slm_openrouter_cost_usd_total{model="X"' in text

    _patch_all_sources(monkeypatch, usage_totals={})
    await metrics_export.refresh()
    text = metrics_core.render()[0].decode("utf-8")
    assert 'model="X"' not in text


async def test_dependency_up_series_disappears_once_source_stops_reporting_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dependency that stops appearing in `probe_dependencies()`'s result
    (e.g. `_PROBES` shrinks, or the source degrades to `{}` on total
    failure) must not freeze at its last observed up/down value — same
    stale-series contract as every other gauge `refresh()` writes."""
    _patch_all_sources(monkeypatch, dependency_up={"postgres": True, "redis": False})
    await metrics_export.refresh()
    text = metrics_core.render()[0].decode("utf-8")
    assert 'slm_dependency_up{dependency="postgres"}' in text
    assert 'slm_dependency_up{dependency="redis"}' in text

    _patch_all_sources(monkeypatch, dependency_up={})
    await metrics_export.refresh()
    text = metrics_core.render()[0].decode("utf-8")
    assert 'dependency="postgres"' not in text
    assert 'dependency="redis"' not in text


def _job_failure_pairs(text: str) -> set[tuple[str, str]]:
    families = _rendered_families(text)
    return {
        (sample.labels["type"], sample.labels["error_type"])
        for sample in families["slm_job_failures"].samples
    }


async def test_job_failures_series_disappears_once_source_stops_reporting_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same stale-series contract as `test_dependency_up_series_disappears_once_source_stops_reporting_it`,
    applied to `slm_job_failures`."""
    _patch_all_sources(monkeypatch, job_failures={("training", "oom"): 2})
    await metrics_export.refresh()
    assert ("training", "oom") in _job_failure_pairs(metrics_core.render()[0].decode("utf-8"))

    _patch_all_sources(monkeypatch, job_failures={})
    await metrics_export.refresh()
    assert ("training", "oom") not in _job_failure_pairs(
        metrics_core.render()[0].decode("utf-8")
    )


# =============================================================================
# 3. Per-gauge value mapping
# =============================================================================


async def test_worker_up_reflects_probe_verdict_per_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_all_sources(monkeypatch, worker_queues={"gpu": False, "cpu": True})
    await metrics_export.refresh()

    families = _rendered_families(metrics_core.render()[0].decode("utf-8"))
    samples = _samples_by_label(families["slm_worker_up"], "queue")
    assert samples["gpu"] == 0.0
    assert samples["cpu"] == 1.0


async def test_queue_depth_exports_zero_valued_series(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_all_sources(monkeypatch, queue_depths={"gpu": 3, "cpu": 0})
    await metrics_export.refresh()

    families = _rendered_families(metrics_core.render()[0].decode("utf-8"))
    samples = _samples_by_label(families["slm_queue_depth"], "queue")
    # The zero-depth "cpu" series must still be present, not omitted because
    # it's falsy — an absent series and a zero-value series mean different
    # things to a dashboard/alert (no worker reporting vs. genuinely idle).
    assert samples == {"gpu": 3.0, "cpu": 0.0}


async def test_dependency_up_reflects_probe_verdict_per_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """True -> 1.0, False -> 0.0, per dependency — mirrors
    `test_worker_up_reflects_probe_verdict_per_queue` for the sibling gauge
    that reuses `readiness.probe_dependencies()` instead of
    `probe_worker_queues()`."""
    _patch_all_sources(
        monkeypatch,
        dependency_up={"postgres": True, "redis": True, "minio": False},
    )
    await metrics_export.refresh()

    families = _rendered_families(metrics_core.render()[0].decode("utf-8"))
    samples = _samples_by_label(families["slm_dependency_up"], "dependency")
    assert samples == {"postgres": 1.0, "redis": 1.0, "minio": 0.0}


async def test_dependency_up_exports_exactly_one_series_per_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`readiness._PROBES` currently has 3 entries (postgres, redis, minio)
    — after a refresh, exactly 3 `slm_dependency_up` series must render, one
    per dependency, never fewer (a dropped dependency) or more (a stale
    series left over from a previous refresh)."""
    _patch_all_sources(
        monkeypatch,
        dependency_up={"postgres": True, "redis": False, "minio": True},
    )
    await metrics_export.refresh()

    families = _rendered_families(metrics_core.render()[0].decode("utf-8"))
    assert len(families["slm_dependency_up"].samples) == 3


async def test_job_failures_exports_all_24_type_error_type_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`job_failure_counts()` zero-fills all `len(_JOB_TYPES) * len(ERROR_TYPES)`
    = 4 x 6 = 24 (type, error_type) pairs on success (see its docstring) — a
    successful refresh must render all 24 series, not just the non-zero
    ones, so a series never looks like it silently vanished during a zero
    stretch."""
    job_types = ("dataset", "training", "evaluation", "export")
    error_types = ("oom", "provider", "storage", "cancelled", "orphaned", "other")
    all_pairs = {
        (job_type, error_type): 0 for job_type in job_types for error_type in error_types
    }
    all_pairs[("training", "oom")] = 5

    _patch_all_sources(monkeypatch, job_failures=all_pairs)
    await metrics_export.refresh()

    families = _rendered_families(metrics_core.render()[0].decode("utf-8"))
    samples = families["slm_job_failures"].samples
    assert len(samples) == 24
    pairs = {(sample.labels["type"], sample.labels["error_type"]): sample.value for sample in samples}
    assert pairs[("training", "oom")] == 5.0
    assert pairs[("dataset", "provider")] == 0.0


async def test_breaker_state_gauge_matches_source_value(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_all_sources(monkeypatch, breaker_state=2)
    await metrics_export.refresh()

    families = _rendered_families(metrics_core.render()[0].decode("utf-8"))
    assert families["slm_openrouter_breaker_state"].samples[0].value == 2.0


# =============================================================================
# 4. Wall-clock bound
# =============================================================================


async def test_refresh_bounds_wall_clock_when_a_source_hangs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_all_sources(monkeypatch)

    async def _hang() -> dict[str, int]:
        await asyncio.sleep(10)  # far longer than the shrunk bound below
        return {"gpu": 1}

    monkeypatch.setattr(metrics_sources, "queue_depths", _hang)
    # Shrink the bound so the test doesn't itself take ~10s to prove the
    # timeout path works — this is the same module attribute `refresh()`
    # reads via `asyncio.wait_for(..., timeout=REFRESH_TIMEOUT_SECONDS)`.
    monkeypatch.setattr(metrics_export, "REFRESH_TIMEOUT_SECONDS", 0.05)

    start = time.monotonic()
    await metrics_export.refresh()  # must not raise or hang
    elapsed = time.monotonic() - start

    assert elapsed < 5.0  # generous margin over the 0.05s bound, far under the 10s hang
