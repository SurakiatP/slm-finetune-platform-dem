# Prompt Template: Add a New Celery Task

> Use this when adding async work that runs on the worker (SDG, training, HPO, evaluation, export).

---

```
Add a Celery task `<task_name>` in `workers/tasks/<area>.py`.

Context:
- Read `CLAUDE.md`, `docs/adr/ADR-004-celery-not-bgtasks.md` first.
- Read `docs/standards/CODING_STYLE.md` for logging / error patterns.
- This task <generates synthetic data | trains a model | runs HPO | runs evaluation | exports a model>.

Inputs (passed through `task.delay(...)` from the router):
- <param1: type>
- <param2: type>

Behavior:
1. Mark the job row in DB as `status=RUNNING`
2. <core domain logic — call into `ai_engine/<area>/...`>
3. Periodically publish progress to Redis channel `job:{self.request.id}`:
   - Message shape: `{"type": "<sdg_progress | training_progress | ...>", "job_id": "...", ...task-specific fields}`
   - Use `redis_client.publish(channel, json.dumps(msg))`
4. On success: mark job `status=COMPLETED`, publish `{"type": "completed", ...}`
5. On failure: mark job `status=FAILED`, publish `{"type": "failed", "error": "..."}`

GPU rules (if this task uses the GPU):
- Wrap the body in `try/finally`
- In `finally`: `del model`, `torch.cuda.empty_cache()`, `gc.collect()`

Bind + retry:
- `@celery_app.task(bind=True, max_retries=0)` — no automatic retry for long jobs
- For idempotent helpers, allow retries via `tenacity`

Tests:
- In `tests/workers/tasks/test_<area>.py`:
  - Mock the `ai_engine` call and the Redis publisher
  - Assert the right messages are published in the right order
  - Assert DB job row transitions PENDING → RUNNING → COMPLETED on happy path
  - Assert RUNNING → FAILED on exception

Definition of Done:
- Task is registered (importable from `workers/celery_app.py`)
- Tests pass with mocked external deps
- WebSocket consumer (`/ws/jobs/{job_id}`) receives messages — verify with the integration test in Phase 8
- Update `WORKING_LOG.md` and `TASK_TRACKER.md`
```

---

## Reminders

- **Never** import `BackgroundTasks` here. Always Celery (ADR-004).
- **The Celery `task.id` IS the `job_id`.** Don't generate a separate UUID.
- **Workers are sync** — wrap async helpers with `asyncio.run(...)` if you must call them.
- **GPU concurrency = 1.** Don't spawn parallel sub-tasks that compete for the GPU.
