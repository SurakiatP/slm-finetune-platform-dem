"""FastAPI application entrypoint.

Wires routers, middleware, OpenAPI metadata, and lifespan callbacks.
The actual business logic lives behind 501 stubs until later phases.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager, suppress
from collections.abc import AsyncIterator

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from api.core import metrics, request_context
from api.core.auth import require_user
from api.core.config import get_settings
from api.core.database import engine
from api.core.exceptions import install_handlers
from api.core.logging_config import configure_logging
from api.services import idempotency, job_reconcile, metrics_export, readiness
from api.routers import (
    datasets,
    evaluations,
    inference,
    jobs,
    models,
    projects,
    tasks_meta,
    trainings,
    usage,
    websocket,
)

settings = get_settings()

configure_logging(settings.log_level)
log = logging.getLogger("api")


# ---- Lifespan -------------------------------------------------------------


_RECONCILE_INTERVAL_SECONDS = 300


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log.info(
        "api starting (env=%s, db=%s, redis=%s)",
        settings.environment,
        _redact(settings.database_url),
        settings.redis_url,
    )

    # Emitted here rather than from the validator itself: settings are built at
    # import time, before `configure_logging()` runs, so warning from inside
    # `Settings` would bypass the JSON formatter or be dropped outright.
    for warning in settings.startup_warnings():
        log.warning("config: %s", warning)

    # Recover jobs whose worker died. `run_forever` sweeps once immediately
    # and then on an interval; it is started as a task rather than awaited
    # here because its first act is a Celery `inspect()` broadcast, which
    # blocks for its full timeout when the broker is unreachable. Awaiting
    # that would delay readiness on every boot and make the API's startup
    # depend on the broker's health — exactly the coupling this feature
    # exists to survive.
    #
    # It lives in the API process rather than a Celery beat container for the
    # same reason: what it detects is "no worker is running this", so the
    # detector must not need a healthy worker fleet to run at all.
    app.state.reconcile_task = asyncio.create_task(
        job_reconcile.run_forever(interval_seconds=_RECONCILE_INTERVAL_SECONDS),
        name="job-reconcile-loop",
    )

    yield

    log.info("api shutting down — stopping reconcile loop and disposing DB engine")
    app.state.reconcile_task.cancel()
    with suppress(asyncio.CancelledError):
        await app.state.reconcile_task
    await engine.dispose()


def _redact(url: str) -> str:
    """Hide credentials in a DSN before logging it."""
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" in rest:
        creds, host = rest.split("@", 1)
        creds = creds.split(":", 1)[0] + ":***"
        return f"{scheme}://{creds}@{host}"
    return url


# ---- App ------------------------------------------------------------------


_OPENAPI_TAGS = [
    {"name": "projects", "description": "Top-level grouping for datasets and trainings."},
    {"name": "datasets", "description": "Seed uploads and synthetic data generation."},
    {"name": "trainings", "description": "Manual and HPO fine-tuning jobs."},
    {"name": "models", "description": "Trained model artifacts; export to GGUF / SafeTensors."},
    {"name": "inference", "description": "OpenAI-compatible inference (proxied to Ollama)."},
    {"name": "evaluations", "description": "Per-task metrics and LLM-as-judge scoring."},
    {"name": "usage", "description": "OpenRouter usage/cost events and monthly rollups."},
    {"name": "jobs", "description": "Job progress snapshots (last WS frame per job, via Redis)."},
    {"name": "metadata", "description": "Static catalogs powering frontend dynamic forms."},
    {"name": "system", "description": "Health, readiness, and infrastructure probes."},
]


app = FastAPI(
    title="SLM Fine-Tuning Platform",
    version="0.1.0",
    description=(
        "Backend-only PoC for an automated Small Language Model fine-tuning platform. "
        "Generates synthetic data via OpenRouter, fine-tunes ≤3B models with Unsloth + QLoRA, "
        "tracks every run in MLflow, and serves results via Ollama."
    ),
    openapi_tags=_OPENAPI_TAGS,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# CORS — open methods/headers, but constrain origins (no credentials per ADR-005 / require.md).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.api_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Host-header allowlist. Registered after CORSMiddleware (so it sits between
# CORS and the outermost request-context middleware below — see that
# middleware's docstring for why it must not be the outermost layer here) and
# before `_request_context_middleware` (so a request-id is still minted and
# the rejection still gets logged even when TrustedHostMiddleware 400s it).
#
# `settings.api_allowed_hosts` is owned by api/core/config.py (do not edit
# that file from here) and defaults to `["*"]`. A production
# `API_ALLOWED_HOSTS` MUST include `localhost`, `127.0.0.1`, and `api` — omit
# any of those and the deploy script's Phase 7 curl (`localhost`) and in-network
# probes get rejected with 400, which looks like the app is down when it is
# actually this guard doing its job.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.api_allowed_hosts)


# Registered after CORSMiddleware and TrustedHostMiddleware so it ends up
# outermost in the middleware stack (Starlette wraps middleware in reverse
# registration order — see `Starlette.add_middleware`, which inserts each new
# middleware at index 0 of `user_middleware`, and `build_middleware_stack`,
# which makes index 0 the outermost layer) — the request id is minted/bound
# before CORS, TrustedHost, or anything downstream runs, and the
# X-Request-ID response header survives every layer beneath it, including a
# TrustedHostMiddleware 400. `tests/unit/test_middleware_ordering.py` asserts
# this from `app.user_middleware` directly rather than trusting this comment.
@app.middleware("http")
async def _request_context_middleware(request: Request, call_next):
    # Truncated to the width of `audit_events.request_id` (String(64)).
    # Without this cap an inbound header is stored verbatim, and since the
    # audit INSERT deliberately rides the caller's transaction with no
    # try/except, one oversized header would make Postgres raise
    # StringDataRightTruncation and take the *mutation* down with it — a
    # single request header turning off project create, job submit and every
    # cancel. sqlite does not enforce VARCHAR width, so no test would have
    # caught it.
    request_id = (request.headers.get("X-Request-ID") or "").strip()[:64] or uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    start = time.perf_counter()
    status_code = 500
    # `request_log_scope()` must wrap `call_next`: BaseHTTPMiddleware runs the
    # endpoint in a separate anyio task, so contextvars set there (e.g.
    # `ownership._bind_log_project`) never reach this frame — the shared
    # scope dict is the only channel back into the access-log line below.
    with request_context.bound(request_id=request_id), request_context.request_log_scope():
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            elapsed_seconds = time.perf_counter() - start
            duration_ms = round(elapsed_seconds * 1000, 2)
            # Prometheus HTTP instrumentation lives here — inside the existing
            # outermost middleware — rather than as a second `@app.middleware`
            # layer. `Starlette.add_middleware` (and this decorator) both
            # `insert(0, ...)`, so any *new* middleware registered would become
            # the new outermost layer and break
            # `test_request_context_middleware_is_outermost`. `status_code`
            # is already 500 by default and only overwritten on the success
            # path above, so a `call_next` that raises is recorded as a 500
            # here too, same as the log line below it.
            metrics.observe_http(
                metrics.resolve_route_label(request.scope, app),
                request.method,
                status_code,
                elapsed_seconds,
            )
            # Sourced from `idempotency.client_host` — the exact same function
            # the dedupe key uses — so this log line shows the idempotency
            # bucket a caller falls into, not just "some" address. Behind
            # cloudflared every request arrives at `edge` from one container
            # IP; if `real_ip`/XFF trust there is ever misconfigured,
            # `client_host` returns that same container IP for every
            # anonymous caller, and every one of them collapses into a single
            # 60s dedupe bucket — user B's submit silently replays user A's
            # 202 instead of enqueueing, swallowing a job with no error
            # anywhere. That is invisible in CI (no edge in the loop) and
            # invisible in a passing test suite; this field turns it into a
            # one-command check on the box:
            #   docker compose logs api | grep client_ip
            # A `172.x`/`10.x` value there for varied external callers means
            # `real_ip` is not matching and the edge config needs fixing.
            log.info(
                "%s %s %s",
                request.method,
                request.url.path,
                status_code,
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "client_ip": idempotency.client_host(request),
                },
            )


# Uniform ErrorResponse for HTTPException, validation errors, and unhandled exceptions.
install_handlers(app)


# ---- Routers --------------------------------------------------------------

API_V1 = "/api/v1"

_AUTH = [Depends(require_user)]

# Router-level (not per-route) so a new route added to any of these seven
# resources is protected by default — nobody has to remember to add the
# dependency on the next endpoint. tasks_meta (3 static-catalog routers),
# `/`, `/health`, `/docs`, `/redoc`, `/openapi.json` stay public: no DB,
# nothing user-scoped. `jobs`/`websocket` job-stream authorization is a
# separate, job_id-keyed concern (celery_task_id -> owner resolution) owned
# elsewhere, not this router-level `Depends`.
app.include_router(
    projects.router, prefix=f"{API_V1}/projects", tags=["projects"], dependencies=_AUTH
)
app.include_router(
    datasets.router, prefix=f"{API_V1}/datasets", tags=["datasets"], dependencies=_AUTH
)
app.include_router(
    trainings.router, prefix=f"{API_V1}/trainings", tags=["trainings"], dependencies=_AUTH
)
app.include_router(
    models.router, prefix=f"{API_V1}/models", tags=["models"], dependencies=_AUTH
)
app.include_router(
    inference.router, prefix=f"{API_V1}/inference", tags=["inference"], dependencies=_AUTH
)
app.include_router(
    evaluations.router,
    prefix=f"{API_V1}/evaluations",
    tags=["evaluations"],
    dependencies=_AUTH,
)
app.include_router(
    usage.router, prefix=f"{API_V1}/usage", tags=["usage"], dependencies=_AUTH
)
app.include_router(tasks_meta.tasks_router, prefix=f"{API_V1}/tasks", tags=["metadata"])
app.include_router(tasks_meta.base_models_router, prefix=f"{API_V1}/base-models", tags=["metadata"])
app.include_router(tasks_meta.sdg_pipeline_router, prefix=f"{API_V1}/sdg-pipeline", tags=["metadata"])
app.include_router(jobs.router, prefix=f"{API_V1}/jobs", tags=["jobs"])
app.include_router(websocket.router)  # WS lives at /ws/jobs/{job_id}


# ---- System ---------------------------------------------------------------


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {
        "name": "slm-platform",
        "version": app.version,
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


@app.get("/health", tags=["system"], summary="Liveness probe")
async def health() -> dict[str, str]:
    """Is this process alive? Deliberately touches nothing else.

    A supervisor restarts the container when this fails, so it must never
    depend on Postgres, Redis, MinIO or the worker — otherwise one dependency
    blip restarts an API that was serving fine. `/ready` is the endpoint that
    asks about dependencies.
    """
    return {"status": "ok"}


@app.get("/ready", tags=["system"], summary="Readiness probe")
async def ready(response: Response) -> dict[str, object]:
    """Can this instance serve? Probes Postgres, Redis, MinIO, and the
    worker_gpu / worker_cpu queues.

    `503` only when Postgres or Redis is down — those are the two the API
    cannot answer a single request without. MinIO or a missing worker_gpu /
    worker_cpu consumer reports `degraded` on a `200`, because the API still
    serves reads and still accepts submits; see `api/services/readiness.py`
    for why failing on those would be the bigger outage.
    """
    report = await readiness.check()
    if not report.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return report.as_dict()


@app.get("/metrics", tags=["system"], summary="Prometheus scrape endpoint")
async def metrics_endpoint() -> Response:
    """Prometheus text-exposition snapshot of this process and the worker fleet.

    No auth dependency — same reasoning as `/health` and `/ready`: a
    Prometheus scraper has no token to send, so requiring one would mean the
    scrape always fails and this instance is invisible to monitoring.
    Refreshes the scrape-time gauges (queue depth, worker_gpu/worker_cpu, job
    counts, OpenRouter usage) via `metrics_export.refresh()` before
    rendering. `refresh()` is contractually not allowed to raise (see its own
    docstring) — this `try`/`except` is a second, belt-and-braces layer on
    top of that contract, same reasoning `refresh()` itself already applies
    one layer down: an observability endpoint 500ing because one gauge
    source is down would be strictly worse than serving the exposition
    format with that gauge left at its last known value.
    """
    try:
        await metrics_export.refresh()
    except Exception:
        log.warning("GET /metrics: refresh() raised, serving last known gauge state", exc_info=True)
    body, content_type = metrics.render()
    return Response(content=body, media_type=content_type)
