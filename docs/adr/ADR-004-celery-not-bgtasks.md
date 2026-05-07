# ADR-004: Celery for Async Work, Not FastAPI BackgroundTasks

**Status:** Accepted
**Confidence:** High
**Date:** 2026-05-07
**Supersedes:** —

## Context + Decision Drivers

The platform has long-running operations: SDG (minutes), training (tens of minutes to hours), HPO (hours), evaluation (minutes). They:

- Cannot block the API event loop
- Need progress reporting via WebSocket
- May need to be cancelled, retried, or inspected from another process
- Run on the **GPU worker container**, not the API container
- Survive an API restart

FastAPI's `BackgroundTasks` runs in the same process as the API. That fails every requirement above.

## Decision

- All async work runs as **Celery tasks** under a dedicated worker container
- Broker + result backend: **Redis**
- The API enqueues tasks and returns the **Celery task ID as the `job_id`** to the client
- Workers publish progress to Redis channel `job:{job_id}` via `redis.publish(...)`
- The `/ws/jobs/{job_id}` WebSocket subscribes to that channel and forwards messages

## Alternatives Considered

- **FastAPI `BackgroundTasks`** — rejected; same process as API, no GPU isolation, no cross-container observability
- **`asyncio.create_task` in API** — rejected; same as above plus no persistence
- **Custom thread pool** — rejected; reinvents Celery
- **arq / Dramatiq** — rejected per ADR-001 (locked stack)

## AI Instructions

**Guidance Level: STRICT**

- **NEVER** import `fastapi.BackgroundTasks` for SDG, training, HPO, evaluation, or model-export work
- Long work goes in `workers/tasks/<area>.py` as a `@celery_app.task` function
- The router handler:
  1. Validates input
  2. Persists a job row in the DB
  3. Calls `celery_app.send_task(...)` (or `task.delay(...)`)
  4. Returns `job_id = task.id` immediately
- Inside a Celery task, publish progress like:
  ```python
  redis_client.publish(f"job:{self.request.id}", json.dumps(message))
  ```
- Always wrap GPU work in `try/finally` and clean up CUDA in the finally block (see ADR-002)
- Use `bind=True` in `@celery_app.task(bind=True)` so the task can read `self.request.id`

## Consequences

✅ API stays responsive even during 4-hour HPO runs
✅ GPU worker isolated; can be restarted independently
✅ Task IDs are stable, queryable, and double as job IDs
✅ Progress streaming is decoupled from the worker that produced it
⚠️ One more service to run (Celery worker) — already in compose
⚠️ Redis is now load-bearing for progress streaming
