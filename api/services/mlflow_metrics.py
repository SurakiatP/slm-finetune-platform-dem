"""Thin async wrapper around the MLflow REST API for the metrics endpoints.

Why REST and not the python `mlflow` SDK: the SDK pulls in a heavy import
graph (sqlalchemy already loaded but plus pandas, mlflow models infra).
We only need three calls — runs/get, metrics/get-history, runs/search —
so a small httpx client is cheaper to load and reason about.

All functions are async + parallel-safe: callers can `asyncio.gather` over
multiple metric keys and the underlying httpx pool handles concurrency.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from api.core.config import get_settings

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class MetricPointDC:
    """One metric data point. Step + value + epoch-millis timestamp."""

    step: int
    value: float
    timestamp_ms: int


def _base_url() -> str:
    return str(get_settings().mlflow_tracking_uri).rstrip("/")


async def get_run(run_id: str, *, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Return full `RunData` (metrics last-values + params + tags)."""
    own = client is None
    c = client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_S)
    try:
        resp = await c.get(f"{_base_url()}/api/2.0/mlflow/runs/get", params={"run_id": run_id})
        resp.raise_for_status()
        return resp.json()["run"]
    finally:
        if own:
            await c.aclose()


async def get_metric_history(
    run_id: str,
    metric_key: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[MetricPointDC]:
    """Return the full step-by-step history for one metric key."""
    own = client is None
    c = client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_S)
    try:
        resp = await c.get(
            f"{_base_url()}/api/2.0/mlflow/metrics/get-history",
            params={"run_id": run_id, "metric_key": metric_key},
        )
        resp.raise_for_status()
        rows = resp.json().get("metrics", [])
        return [
            MetricPointDC(
                step=int(r.get("step", 0)),
                value=float(r["value"]),
                timestamp_ms=int(r.get("timestamp", 0)),
            )
            for r in rows
        ]
    finally:
        if own:
            await c.aclose()


async def search_child_runs(
    parent_run_id: str,
    experiment_id: str,
    *,
    client: httpx.AsyncClient | None = None,
    max_results: int = 50,
) -> list[dict[str, Any]]:
    """Return runs whose `mlflow.parentRunId` tag points to `parent_run_id`."""
    own = client is None
    c = client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_S)
    try:
        resp = await c.post(
            f"{_base_url()}/api/2.0/mlflow/runs/search",
            json={
                "experiment_ids": [experiment_id],
                "filter": f"tags.`mlflow.parentRunId` = '{parent_run_id}'",
                "max_results": max_results,
            },
        )
        resp.raise_for_status()
        return list(resp.json().get("runs", []))
    finally:
        if own:
            await c.aclose()


def extract_metric_keys(run: dict[str, Any]) -> list[str]:
    """Pull metric key names out of a `runs/get` payload."""
    metrics = run.get("data", {}).get("metrics", []) or []
    return sorted({m["key"] for m in metrics if "key" in m})


def extract_params(run: dict[str, Any]) -> dict[str, str]:
    """Flatten run params into a `{key: value}` dict (values stay as strings)."""
    return {p["key"]: p["value"] for p in run.get("data", {}).get("params", []) or []}


def extract_tag(run: dict[str, Any], key: str) -> str | None:
    for t in run.get("data", {}).get("tags", []) or []:
        if t.get("key") == key:
            return t.get("value")
    return None


def extract_metric_last_value(run: dict[str, Any], metric_key: str) -> float | None:
    for m in run.get("data", {}).get("metrics", []) or []:
        if m.get("key") == metric_key:
            return float(m["value"])
    return None


__all__ = [
    "MetricPointDC",
    "get_run",
    "get_metric_history",
    "search_child_runs",
    "extract_metric_keys",
    "extract_params",
    "extract_tag",
    "extract_metric_last_value",
]
