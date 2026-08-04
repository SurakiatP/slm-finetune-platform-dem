"""FastAPI application entrypoint.

Wires routers, middleware, OpenAPI metadata, and lifespan callbacks.
The actual business logic lives behind 501 stubs until later phases.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.core.config import get_settings
from api.core.database import engine
from api.core.exceptions import install_handlers
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

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("api")


# ---- Lifespan -------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log.info("api starting (db=%s, redis=%s)", _redact(settings.database_url), settings.redis_url)
    yield
    log.info("api shutting down — disposing DB engine")
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

# Uniform ErrorResponse for HTTPException, validation errors, and unhandled exceptions.
install_handlers(app)


# ---- Routers --------------------------------------------------------------

API_V1 = "/api/v1"

app.include_router(projects.router, prefix=f"{API_V1}/projects", tags=["projects"])
app.include_router(datasets.router, prefix=f"{API_V1}/datasets", tags=["datasets"])
app.include_router(trainings.router, prefix=f"{API_V1}/trainings", tags=["trainings"])
app.include_router(models.router, prefix=f"{API_V1}/models", tags=["models"])
app.include_router(inference.router, prefix=f"{API_V1}/inference", tags=["inference"])
app.include_router(evaluations.router, prefix=f"{API_V1}/evaluations", tags=["evaluations"])
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
