# ADR-006: asyncpg for Async SQLAlchemy Sessions

**Status:** Accepted
**Confidence:** High
**Date:** 2026-05-08
**Supersedes:** —

## Context + Decision Drivers

[`require.md`](../../require.md) locks the tech stack to "SQLAlchemy 2.0 (async where possible)" and lists `psycopg2-binary` as the only Postgres driver. ADR-001 ratifies that lock and treats it as STRICT.

But "async where possible" is unreachable with `psycopg2-binary` alone:

- `psycopg2` is a **sync-only** driver. SQLAlchemy 2.0's `create_async_engine(...)` requires an async DBAPI. With `postgresql+psycopg2://...` the engine refuses to start.
- The API container handles long-running endpoints (`/datasets/generate`, `/trainings`) that must not block the FastAPI event loop on DB writes.
- Alembic, however, is sync — its migration ops run inside a transaction-aware sync `Connection`, and the recommended `run_migrations_online` flow is sync.
- MLflow's tracking server (separate container) also speaks sync SQLAlchemy via `psycopg2`.

So we need **two drivers running side-by-side**: one async for the API/worker request path, one sync for Alembic + MLflow.

`require.md`'s dep list was missing the async half. Session 1 added it as a strict-additive change and flagged the gap for an ADR; this is that ADR.

## Decision

- **Add `asyncpg>=0.30.0` to base dependencies in `pyproject.toml`** (alongside, not in place of, `psycopg2-binary`).
- **Canonical `DATABASE_URL` uses the async dialect**: `postgresql+asyncpg://user:pass@host:port/db`. This is what `.env.example` and `docker-compose.yml` ship.
- **Sync consumers auto-rewrite the URL** to `postgresql+psycopg2://...` rather than asking the operator to maintain two env vars:
  - `alembic/env.py` — replaces `+asyncpg` with `+psycopg2` before `engine_from_config`.
  - `workers/sync_db.py` — same rewrite for the Celery worker's sync session factory (workers run sync because Unsloth/TRL/MLflow callbacks are sync).
- **Single source of truth for the URL** stays in `api/core/config.py` (`Settings.database_url`), with the rewrite documented inline.
- **`psycopg2-binary` stays in base deps**, not just `[dev]`. Alembic + the worker both need it at runtime, not just developer machines.

## Alternatives Considered

- **`psycopg[binary]` (psycopg3)** — has both sync and async APIs in one package. Tempting, but rejected: SQLAlchemy 2.0's async support for psycopg3 was still maturing when the stack was locked, and the team's existing patterns (and most StackOverflow answers) assume `asyncpg`. Switching costs > benefit for a PoC.
- **Sync sessions everywhere, drop async** — rejected: defeats `require.md`'s "async where possible" intent and forces FastAPI handlers to either run sync (blocks the event loop on every query) or wrap each call in `run_in_executor` (uglier than just using async).
- **Two separate env vars (`DATABASE_URL` + `DATABASE_URL_SYNC`)** — rejected: doubles the surface area of a config bug. The async URL is strictly more informative (the dialect is part of the URL), so we derive the sync one.
- **Async Alembic via `run_async_migrations`** — possible, but adds complexity Alembic itself doesn't recommend for first-time users. The URL rewrite is one line; the async Alembic flow is ~15 lines of boilerplate.

## AI Instructions

**Guidance Level: STRICT**

- **NEVER** suggest replacing `asyncpg` with another async Postgres driver, or removing `psycopg2-binary` to "consolidate." Both are load-bearing.
- **All FastAPI handler DB access uses the async session** from `api/core/database.py`. Don't introduce a sync engine in API code.
- **Celery workers use the sync session** from `workers/sync_db.py`. Don't introduce an async engine there — Celery's task execution model is sync, and the URL rewrite is what makes this work.
- **`DATABASE_URL` in `.env`/compose stays in `+asyncpg` form.** If you see `+psycopg2` in `.env` or `docker-compose.yml`, fix it back to `+asyncpg` — the sync rewrite is the consumer's job.
- When **adding a new sync DB consumer** (e.g., a one-shot CLI script), reuse the rewrite helper from `workers/sync_db.py` rather than reinventing the regex.
- When **debugging DB connection errors**:
  - "InvalidRequestError: The asyncio extension requires an async driver" → caller is using sync `create_engine` against the async URL; route through the rewrite or use `create_async_engine`.
  - "ModuleNotFoundError: asyncpg" in the API container → the API image didn't install base deps; check `docker/api.Dockerfile`.

## Consequences

✅ FastAPI handlers can `await session.execute(...)` without blocking the event loop, even on a slow query.
✅ Alembic migrations Just Work with the same `DATABASE_URL` operators already configured.
✅ Single canonical URL — operators don't have to remember two formats.
✅ Workers stay sync (matches Unsloth/TRL/MLflow's sync-only nature) without forcing the API to be sync too.
⚠️ Two Postgres drivers in the dep tree (~3MB extra image size). Acceptable.
⚠️ The URL rewrite is a "magic" step — anyone reading `alembic/env.py` or `workers/sync_db.py` cold needs the comment to understand why. Both files document it inline.
⚠️ If the team ever migrates to psycopg3, this ADR must be superseded — the rewrite logic and both deps would change.
