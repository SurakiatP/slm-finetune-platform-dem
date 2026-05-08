# Testing Guide

> How we write, organize, and run tests in this project.

## Stack

- **pytest** — runner
- **pytest-asyncio** — async tests
- **httpx.AsyncClient** — API integration tests against `app`
- **pytest-mock** — mocking
- **factory-boy** or simple fixtures — test data
- **testcontainers** (optional, Phase 8) — real Postgres / Redis for integration

## Layout

Mirror the source tree:

```
tests/
├── conftest.py                # Top-level fixtures
├── api/
│   ├── routers/
│   │   ├── test_projects.py
│   │   └── test_datasets.py
│   └── schemas/
│       └── test_training.py   # Pydantic validator coverage
├── workers/
│   └── tasks/
│       └── test_data_generation.py
└── ai_engine/
    ├── data_gen/
    │   └── test_validators.py
    └── training/
        └── test_data_formatters.py
```

## Test Categories

| Category | Mark | Scope |
|----------|------|-------|
| **Unit** | none (default) | Single function / class, no I/O |
| **Integration** | `@pytest.mark.integration` | Hits real DB / Redis / MinIO |
| **GPU** | `@pytest.mark.gpu` | Requires CUDA — skipped in CI without GPU |
| **External** | `@pytest.mark.external` | Hits OpenRouter, Ollama — skipped by default |

Run subsets:
```bash
pytest                                  # unit only (fast)
pytest -m integration                   # integration
pytest -m "not gpu and not external"    # everything except slow/external
```

## Naming

- Files: `test_<module>.py`
- Functions: `test_<what>_<condition>` — e.g. `test_sdg_request_rejects_seed_below_5`
- Class-based grouping when many cases share setup

## Fixtures

Top-level `conftest.py` provides:
- `client` — `httpx.AsyncClient` bound to the FastAPI app
- `db_session` — async SQLAlchemy session against an in-memory or test DB
- `redis_client` — fakeredis or real Redis depending on mark
- `mock_openrouter` — patches the OpenRouter client to return canned responses
- `sample_seed_data` per task type

## Mocking Strategy

**Mock at the boundary**, not internals:

| External | How to mock |
|----------|-------------|
| OpenRouter | Patch `ai_engine.data_gen.openrouter_client.OpenRouterClient` |
| Celery enqueue | Patch `celery_app.send_task` and inspect calls |
| MinIO | Patch the minio client at the service layer |
| MLflow | Use `MlflowClient` against a temp `mlflow://./tmp_mlruns` |
| GPU (training) | Mark as `@pytest.mark.gpu` and skip if no CUDA |

Don't mock SQLAlchemy — use a real test DB (sqlite for unit, postgres for integration).

## What MUST be tested

1. **Pydantic validators** — every `model_validator` / `field_validator` has a positive and negative case
2. **Per-task validators** in `ai_engine/data_gen/validators.py` — bad samples must raise
3. **Data formatters** — input → expected formatted string, all 3 task types
4. **Deduplication** — known dupes are removed, non-dupes preserved
5. **API endpoints** — happy path + 404 + 422 for every route
6. **Discriminated unions** — `with_seed` requires `seed_data`, `description_only` + `classification` requires `labels`, etc.
7. **Celery task happy paths** — at least one integration test per task with mocks
8. **WebSocket** — connect, receive a published progress message, disconnect

## What we DON'T need to test

- Trivial getters / passthroughs
- Third-party libraries (Pydantic, FastAPI)
- One-line wrappers around `logging`

## Coverage Target

- Schemas + validators: **100%**
- `ai_engine/`: ≥85%
- API routers: ≥80% (each route has at least happy + one error)
- Overall: ≥75%

`pytest --cov=api --cov=workers --cov=ai_engine --cov-report=term-missing`

## Test Data

- Realistic but minimal — 3–5 samples per task type is enough for unit tests
- Live in `tests/fixtures/` as JSON files when reused across tests
- For training tests: tiny synthetic dataset (10 samples), tiny base model alias

## Async Tests

```python
import pytest

@pytest.mark.asyncio
async def test_create_project(client):
    r = await client.post("/api/v1/projects", json={"name": "demo"})
    assert r.status_code == 201
```

Configure once in `pyproject.toml`:
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

## Definition of Done (per PR)

- [ ] All tests pass locally (`pytest`)
- [ ] No new `# type: ignore` without a reason comment
- [ ] Coverage didn't drop on touched modules
- [ ] No `print` / commented code / `breakpoint()` left behind
