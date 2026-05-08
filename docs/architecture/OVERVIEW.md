# Architecture Overview

## System Goals

A **single-node** PoC that turns user intent (task description + optional seed) into a fine-tuned SLM, with full experiment tracking and OpenAI-compatible inference.

## High-Level Flow

```
User → FastAPI ──► PostgreSQL (state)
         │
         ├──► Celery (Redis broker) ──► Worker (GPU)
         │                                 │
         │                                 ├── ai_engine.data_gen (OpenRouter)
         │                                 ├── ai_engine.training (Unsloth + QLoRA)
         │                                 ├── ai_engine.hpo (Optuna)
         │                                 └── ai_engine.evaluation
         │
         ├──► MinIO (datasets, model artifacts)
         ├──► MLflow (params, metrics, model registry)
         └──► Redis Pub/Sub ──► WebSocket /ws/jobs/{id}
                                       ▲
                                       └─ Frontend (teammate, separate)

Trained model ──► Ollama (GGUF) ──► OpenAI-compatible inference API
```

## Hexagonal Discipline

**Domain (no infra dependencies):** `ai_engine/`
- `data_gen/` — pure SDG logic + OpenRouter client (only external SDK)
- `training/` — Unsloth + HF Trainer wrappers
- `hpo/` — Optuna search
- `evaluation/` — metrics + LLM judge

**Application (orchestration):** `workers/tasks/`
- Celery tasks that compose `ai_engine` + persistence + progress publishing

**Adapters (HTTP / DB / queue):** `api/`
- FastAPI routers in `api/routers/`
- Pydantic schemas in `api/schemas/`
- SQLAlchemy ORMs in `api/models/`
- Reusable services in `api/services/`

The rule: `ai_engine` may NOT import from `api/` or `workers/`. Adapters import inward, never outward.

## Synchronous vs Asynchronous

| Sync (HTTP request → response) | Async (Celery → progress) |
|--------------------------------|---------------------------|
| CRUD on Project / Dataset metadata | SDG generation |
| Triggering training (returns job_id) | Manual training |
| Reading status / metadata | HPO training |
| Inference (chat/completions) | Evaluation runs |

Async work publishes progress to Redis channel `job:{job_id}`, which the WebSocket
endpoint subscribes to and forwards to the frontend.

## Database Entities (high level)

```
Project ─┬─ Dataset (seed | generated)
         ├─ TrainingJob ─── ModelArtifact
         └─ EvaluationRun ── (refs ModelArtifact + Dataset)
```

`TrainingJob` and other long-running entities use **Celery task IDs as job IDs** —
do not generate separate identifiers (see require.md note 6).

## Storage Layout

| What | Where |
|------|-------|
| Application state | PostgreSQL |
| Seed datasets (uploaded) | MinIO bucket `datasets/seed/` |
| Generated datasets | MinIO bucket `datasets/generated/` |
| Model artifacts (LoRA, GGUF) | MinIO bucket `models/` |
| MLflow artifacts | MinIO bucket `mlflow/` |
| Cache (HF, Unsloth) | Local volume mounted into worker |

## GPU Resource Constraint

- Single RTX 3060 12GB
- Worker container reserves 1 GPU; Ollama also requires GPU
- One training job at a time (worker concurrency = 1 for GPU tasks)
- Always call `torch.cuda.empty_cache()` and delete model objects on task exit
