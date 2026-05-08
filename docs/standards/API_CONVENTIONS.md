# API Conventions

> Rules for FastAPI routers, paths, status codes, and the OpenAPI surface that the frontend consumes.

## URL Style

- All endpoints are prefixed with `/api/v1`
- Resources are **plural nouns**: `/projects`, `/datasets`, `/trainings`, `/models`, `/evaluations`
- Sub-resources use nesting: `/datasets/{id}/preview`, `/trainings/{id}/mlflow-url`
- WebSocket endpoints under `/ws/...`
- Use kebab-case for multi-word actions: `/upload-seed`, `/mlflow-url`
- Path params are integers or UUIDs, never natural keys

## HTTP Methods

| Verb | Use |
|------|-----|
| `GET` | Read |
| `POST` | Create / trigger |
| `PATCH` | Partial update |
| `PUT` | Full replace (rare; prefer PATCH) |
| `DELETE` | Remove or cancel a job |

## Status Codes

| Code | When |
|------|------|
| 200 | Successful read |
| 201 | Successful create |
| 202 | Accepted; work runs async (return `job_id`) |
| 204 | Successful delete (no body) |
| 400 | Client error not covered by validation (e.g., business rule) |
| 404 | Resource not found |
| 409 | Conflict (e.g., name already taken, model not ready) |
| 422 | Pydantic validation error (FastAPI default) |
| 500 | Unhandled server error (log full stack, return safe message) |
| 501 | Endpoint not yet implemented (Phase 3 skeleton state) |

## Request Shape

- All request bodies are Pydantic models from `api/schemas/`
- File uploads use `multipart/form-data` and `UploadFile`
- Query params use `Query(...)` with `description` for OpenAPI docs
- Pagination: `?limit=20&offset=0` (default 20, max 100)

## Response Shape

- All responses are Pydantic models — never raw dicts
- Async-job endpoints return:
  ```python
  {"job_id": "celery-task-uuid", "status": "pending", "...": "..."}
  ```
- List endpoints return `{"items": [...], "total": N}` — not bare arrays
- Errors follow FastAPI's default `{"detail": "..."}` shape

## Async Job Pattern

For any endpoint that triggers long work (SDG, training, HPO, evaluation, export):

1. Validate input via Pydantic
2. Create a job row in DB with `status=PENDING`
3. Enqueue Celery task: `task = celery_app.send_task("workers.tasks.x.run", ...)`
4. Update job row with `task.id`
5. Return **202 Accepted** with `{"job_id": task.id, ...}`
6. The frontend connects to `/ws/jobs/{job_id}` to stream progress

The Celery `task.id` IS the `job_id` — do not generate a separate UUID (require.md note 6).

## WebSocket Messages

All messages are JSON with a discriminator field `type`:

```json
{"type": "sdg_progress", "job_id": "...", "completed": 42, "total": 100}
{"type": "training_progress", "job_id": "...", "step": 100, "loss": 1.23, "lr": 2e-4}
{"type": "hpo_progress", "job_id": "...", "trial": 3, "metric": 0.87, "params": {...}}
{"type": "completed", "job_id": "...", "result": {...}}
{"type": "failed", "job_id": "...", "error": "..."}
```

The `type` field is a discriminator — Pydantic models in `api/schemas/progress.py` use `Field(discriminator="type")`.

## CORS

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=False,  # No auth, no cookies
    allow_methods=["*"],
    allow_headers=["*"],
)
```

Origins come from `Settings.cors_origins` — keep dev defaults but allow override via env.

## OpenAPI Quality

- Every router has `tags=["..."]` for grouping in Swagger UI
- Every endpoint has a one-line `summary` and a longer `description`
- Every Pydantic field has `description` (visible in Swagger)
- Examples on requests/responses via `model_config = ConfigDict(json_schema_extra={"examples": [...]})`
- `app.openapi_url = "/openapi.json"` (default) — this is what the frontend teammate consumes

## Versioning

- Path prefix `/api/v1` is the only versioning strategy
- Breaking changes → new path prefix `/api/v2`, keep v1 alive during deprecation

## Dependency Injection

Use FastAPI `Depends(...)` for:
- Database session: `Depends(get_session)`
- Settings: `Depends(get_settings)`
- Celery client: `Depends(get_celery)`

Define these in `api/core/` so routers stay thin.
