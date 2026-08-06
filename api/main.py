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

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from api.core import request_context
from api.core.auth import require_user
from api.core.config import get_settings
from api.core.database import AsyncSessionLocal, engine
from api.core.exceptions import install_handlers
from api.core.logging_config import configure_logging
from api.services import job_reconcile
from api.routers import (
    datasets,
    evaluations,
    inference,
    jobs,
    models,
    projects,
    tasks_meta,
    trainings,
    websocket,
)

settings = get_settings()

configure_logging(settings.log_level)
log = logging.getLogger("api")


# ---- Lifespan -------------------------------------------------------------


_RECONCILE_INTERVAL_SECONDS = 300


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log.info("api starting (db=%s, redis=%s)", _redact(settings.database_url), settings.redis_url)

    # Sweep once immediately: a restart is the single most likely moment for
    # orphans to exist, because whatever killed the worker often took the API
    # with it. Wrapped in try/except because a broker outage must not block
    # startup — the periodic loop below will catch up once it recovers.
    try:
        async with AsyncSessionLocal() as session:
            report = await job_reconcile.reconcile_once(session)
        log.info(
            "startup reconcile: %d orphan(s) of %d checked%s",
            report.count,
            report.checked,
            f" (aborted: {report.abort_reason})" if report.aborted else "",
        )
    except Exception:  # noqa: BLE001 — never let reconciliation stop the API booting
        log.exception("startup reconcile failed; continuing without it")

    # Then keep sweeping. This lives in the API process rather than a Celery
    # beat container on purpose: the thing being detected is "no worker is
    # running this", so the detector must not itself depend on a healthy
    # worker fleet to run.
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

# Registered after CORSMiddleware so it ends up outermost in the middleware
# stack (Starlette wraps middleware in reverse registration order) — the
# request id is minted/bound before CORS or anything downstream runs, and
# the X-Request-ID response header survives every layer beneath it.
@app.middleware("http")
async def _request_context_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    start = time.perf_counter()
    status_code = 500
    with request_context.bound(request_id=request_id):
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
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
                },
            )


# Uniform ErrorResponse for HTTPException, validation errors, and unhandled exceptions.
install_handlers(app)


# ---- Routers --------------------------------------------------------------

API_V1 = "/api/v1"

_AUTH = [Depends(require_user)]

# Router-level (not per-route) so a new route added to any of these six
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
    return {"status": "ok"}
