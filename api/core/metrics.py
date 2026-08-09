"""Prometheus metrics for the API process and the Celery worker fleet.

Everything here lives on a **module-private** `CollectorRegistry` rather than
`prometheus_client`'s process-global default registry. Two reasons:

  * `render()` must be able to serialize exactly this catalog, deterministically,
    regardless of what any other library happens to register on the default
    registry at import time (mlflow/optuna-adjacent packages sometimes do).
  * Re-importing this module (which pytest does routinely, once per test
    process) must not raise `ValueError: Duplicated timeseries in CollectorRegistry`
    the way re-registering onto the global default would.

No per-tenant label ever appears on any metric below (no `project`, `actor`,
`user`, `job_id`, ...). These are scraped by Prometheus on a fixed interval
and rendered as a flat, low-cardinality snapshot — a label set that grows
with the number of projects/jobs ever created would make every scrape bigger
forever. Anything tenant-scoped belongs in the audit/usage tables, not here.

`prometheus_client` is an API-only dependency (the `[metrics]` extra in
pyproject.toml). The GPU worker image (docker/worker.Dockerfile) does not
install it — this module must therefore never be imported from a
worker-boot module. `tests/unit/test_worker_import_surface.py` guards that
the same way it guards PyJWT.
"""

from __future__ import annotations

import weakref
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

_REGISTRY = CollectorRegistry()

# ---- HTTP -------------------------------------------------------------------

slm_http_requests_total = Counter(
    "slm_http_requests_total",
    "Total HTTP requests handled by the API, by route template.",
    ["route", "method", "status_class"],
    registry=_REGISTRY,
)

slm_http_request_duration_seconds = Histogram(
    "slm_http_request_duration_seconds",
    "HTTP request handling duration in seconds, by route template.",
    ["route", "method", "status_class"],
    registry=_REGISTRY,
)

# ---- Queue / worker fleet -----------------------------------------------

slm_queue_depth = Gauge(
    "slm_queue_depth",
    "Number of tasks currently queued, per Celery queue.",
    ["queue"],
    registry=_REGISTRY,
)

slm_worker_up = Gauge(
    "slm_worker_up",
    "Whether at least one worker is consuming from a queue (1) or not (0).",
    ["queue"],
    registry=_REGISTRY,
)

# ---- Dependencies ---------------------------------------------------------

# Reuses `api/services/readiness.py`'s own `_PROBES` (via `probe_dependencies()`)
# rather than a second hand-rolled set of dependency checks — see ADR-013's
# alerting-decisions section. Currently `postgres`, `redis`, `minio`.
slm_dependency_up = Gauge(
    "slm_dependency_up",
    "Whether a backing dependency answered its readiness probe (1) or not (0).",
    ["dependency"],
    registry=_REGISTRY,
)

# ---- Jobs ---------------------------------------------------------------

slm_jobs = Gauge(
    "slm_jobs",
    "Job count snapshot, by job type and terminal/current outcome.",
    ["type", "outcome"],
    registry=_REGISTRY,
)

slm_job_duration_seconds_avg = Gauge(
    "slm_job_duration_seconds_avg",
    "Average job duration in seconds, by job type.",
    ["type"],
    registry=_REGISTRY,
)

slm_job_duration_seconds_max = Gauge(
    "slm_job_duration_seconds_max",
    "Max job duration in seconds, by job type.",
    ["type"],
    registry=_REGISTRY,
)

# `error_type` is a closed whitelist (`api/services/metrics_sources.ERROR_TYPES`:
# oom, provider, storage, cancelled, orphaned, other), classified scrape-time
# from free-text `error_message` columns — never a raw exception class name,
# which would be unbounded cardinality on a label. See ADR-013's alerting
# decisions section.
slm_job_failures = Gauge(
    "slm_job_failures",
    "Failed job count snapshot, by job type and classified error type.",
    ["type", "error_type"],
    registry=_REGISTRY,
)

# ---- OpenRouter -----------------------------------------------------------

# No labels: this mirrors the single process-wide breaker state derived in
# api/services/circuit_breaker.py (`BreakerState = Literal["closed", "open",
# "half_open"]`), not a per-model/per-project state. Encoding fixed by the
# plan: 0=closed, 1=half_open, 2=open.
slm_openrouter_breaker_state = Gauge(
    "slm_openrouter_breaker_state",
    "OpenRouter circuit breaker state (0=closed, 1=half_open, 2=open).",
    registry=_REGISTRY,
)

slm_openrouter_cost_usd_total = Gauge(
    "slm_openrouter_cost_usd_total",
    "Cumulative OpenRouter spend in USD, by model, pipeline stage, and outcome.",
    ["model", "stage", "outcome"],
    registry=_REGISTRY,
)

slm_openrouter_prompt_tokens_total = Gauge(
    "slm_openrouter_prompt_tokens_total",
    "Cumulative OpenRouter prompt tokens, by model, pipeline stage, and outcome.",
    ["model", "stage", "outcome"],
    registry=_REGISTRY,
)

slm_openrouter_completion_tokens_total = Gauge(
    "slm_openrouter_completion_tokens_total",
    "Cumulative OpenRouter completion tokens, by model, pipeline stage, and outcome.",
    ["model", "stage", "outcome"],
    registry=_REGISTRY,
)


# ---- observe_http -----------------------------------------------------------


def _status_class(status_code: int) -> str:
    """Collapse a numeric status code to its class label ("2xx".."5xx")."""

    return f"{status_code // 100}xx"


def observe_http(route: str, method: str, status_code: int, duration_seconds: float) -> None:
    """Record one completed HTTP request against the counter + histogram above.

    `route` must already be a route *template* (see `resolve_route_label`
    below) — never a raw request path — or the low-cardinality guarantee this
    whole module exists for is gone.
    """

    status_class = _status_class(status_code)
    slm_http_requests_total.labels(route=route, method=method, status_class=status_class).inc()
    slm_http_request_duration_seconds.labels(
        route=route, method=method, status_class=status_class
    ).observe(duration_seconds)


# ---- resolve_route_label -----------------------------------------------


_UNMATCHED_ROUTE = "unmatched"

# endpoint -> path template ("path_format"), cached per-app. Keyed by a weak
# reference to the app instance (not `id(app)`) so a garbage-collected app —
# e.g. one built fresh per test — can't have its slot silently reused by an
# unrelated app that happens to get the same id().
_endpoint_map_cache: weakref.WeakKeyDictionary[Any, dict[Any, str]] = weakref.WeakKeyDictionary()


def _walk_routes(routes: Any, prefix: str, mapping: dict[Any, str]) -> None:
    """Recurse into `APIRouter.include_router()`-included sub-routers.

    On this FastAPI/Starlette version, `app.routes` is NOT a flat list of
    `APIRoute`s — every `include_router(...)` call (every `api/routers/*.py`
    module) leaves a `_IncludedRouter` wrapper on `app.routes` with
    `endpoint = None` and `path_format = None` of its own. The real routes
    live one level down at `route.include_context.included_router.routes`,
    and THEIR `path_format` is relative to `route.include_context.prefix`
    (e.g. sub-route `"/{project_id}"` under prefix `"/api/v1/projects"`).
    A single-level scan (the first version of this function) silently
    mapped 8 top-level routes and left every `/api/v1/*` endpoint — 47 of
    the app's 47 API operations plus the `/ws/jobs/{job_id}` websocket route
    — resolving to "unmatched". Recursing
    (rather than assuming exactly one level of nesting) also survives a
    future router that itself includes a sub-router.
    """
    for route in routes:
        include_context = getattr(route, "include_context", None)
        if include_context is not None:
            _walk_routes(
                include_context.included_router.routes,
                prefix + include_context.prefix,
                mapping,
            )
            continue
        endpoint = getattr(route, "endpoint", None)
        path_format = getattr(route, "path_format", None)
        if endpoint is None or path_format is None:
            continue
        mapping[endpoint] = prefix + path_format


def _build_endpoint_map(app: Any) -> dict[Any, str]:
    mapping: dict[Any, str] = {}
    _walk_routes(getattr(app, "routes", []), "", mapping)
    return mapping


def resolve_route_label(scope: Any, app: Any) -> str:
    """Return the route *template* Starlette matched for this scope.

    `scope["route"].path_format` exists on this Starlette version, but for
    a route reached through `include_router()` it is the path RELATIVE to
    that router's mount prefix (e.g. `"/{project_id}"`, not
    `"/api/v1/projects/{project_id}"`) — using it directly would collide
    every router's `"/{id}"`-shaped routes onto the same label. So instead
    we build (and cache) a map from each route's endpoint callable to its
    fully-prefixed `path_format`, by recursing `app.routes` (see
    `_walk_routes`), and look the request's matched `scope["endpoint"]` up
    in it. Unmatched requests (404s that never hit a route, or scopes built
    by hand without going through the router) get "unmatched" — never the
    raw request path, which would blow up label cardinality with one series
    per distinct URL (including UUIDs, IDs, etc.).
    """

    endpoint = scope.get("endpoint")
    if endpoint is None:
        return _UNMATCHED_ROUTE

    mapping = _endpoint_map_cache.get(app)
    if mapping is None:
        mapping = _build_endpoint_map(app)
        _endpoint_map_cache[app] = mapping

    return mapping.get(endpoint, _UNMATCHED_ROUTE)


# ---- render -----------------------------------------------------------------


def render() -> tuple[bytes, str]:
    """Serialize the private registry in Prometheus text exposition format."""

    return generate_latest(_REGISTRY), CONTENT_TYPE_LATEST
