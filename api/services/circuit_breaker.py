"""Redis-backed circuit breaker for OpenRouter calls (SDG generation + judge).

Breaker state MUST live in Redis, not in-process. The worker is started with
`worker_max_tasks_per_child=1` (see `workers/celery_app.py`), which recycles
the Celery worker process after every single task — an in-memory counter or
"opened at" timestamp would be wiped clean before the next task even starts,
so an in-process breaker would never trip no matter how many times
OpenRouter fails. Redis is the only thing both the API process (which needs
to refuse new submissions while OpenRouter is down) and every short-lived
worker process (which needs to fail fast instead of grinding through
tenacity retries against a dead provider) can agree on.

Policy (ADR-pending, mirrors a standard 3-state breaker):

  * closed   — calls proceed normally. `openrouter_breaker_failure_threshold`
               *consecutive* failures (no intervening success) trips it open.
  * open     — calls are refused outright for `openrouter_breaker_open_seconds`.
  * half_open — once the open window has elapsed, exactly one caller is
               admitted as a probe (`SET NX EX` on a dedicated probe key —
               whichever caller wins the race is the probe, everyone else is
               refused). The probe's outcome decides what's next: success
               closes the breaker and resets the failure count; failure
               re-opens it for a fresh full window.

Two independent call surfaces, matching the two runtimes that need this:

  * `assert_closed()` — async, for the API's job-submit routers. Coarse gate:
    refuses new submissions while definitely `open` (503 + `Retry-After`).
    Deliberately does *not* also enforce the half-open single-probe rule —
    that would require the API to somehow reserve the probe slot without
    itself making the OpenRouter call. `half_open` and `closed` both pass
    the gate; the real single-probe admission happens where the actual call
    happens, in `precheck()`.
  * `precheck()` / `on_failure(exc)` — sync, for the OpenRouter client hooks
    (`ai_engine/data_gen/openrouter_client.py`'s `PrecheckHook` /
    `OnCallFailureHook`, injected from the worker side). These run inside a
    Celery task with no FastAPI error-handling middleware, so `precheck()`
    raises a plain `CircuitOpenError`, never `HTTPException`. `on_failure`
    filters every exception through `is_breaker_failure()` before it counts
    against the breaker — a 4xx that isn't a rate limit is a bad request,
    not an outage, and must not trip the breaker.

Failure mode: any Redis error on either surface is logged and treated as
CLOSED (fail open) — every function below degrades to "let the call/submit
through" rather than raising. This is the same reasoning as
`api/services/idempotency.py`, with a deliberately different cost/benefit:
there, a Redis failure at worst lets through one duplicate job; here, a
Redis failure at worst lets through a few calls to a provider that might be
down (which tenacity + the caller's own error handling already survive).
Both are far cheaper than a broken Redis silently refusing *all* future
work because the breaker got stuck believing itself open (or, worse,
refusing all submissions because it can no longer prove itself closed).
"""

from __future__ import annotations

import logging
import math
import time
from typing import Literal

from fastapi import HTTPException
from redis import Redis

from ai_engine.data_gen.openrouter_client import is_breaker_failure
from api.core.config import get_settings
from api.core.redis_client import get_redis_client

log = logging.getLogger("api.circuit_breaker")

# ---- Redis keys -------------------------------------------------------------

_NAMESPACE = "breaker:openrouter"
_FAILURES_KEY = f"{_NAMESPACE}:failures"
_OPENED_AT_KEY = f"{_NAMESPACE}:opened_at"
_PROBE_KEY = f"{_NAMESPACE}:probe"

BreakerState = Literal["closed", "open", "half_open"]


class CircuitOpenError(Exception):
    """Raised by `precheck()` when a worker-side call must not proceed.

    Plain `Exception`, not `HTTPException` — this runs inside a Celery
    worker, where nothing installs FastAPI's exception handlers.
    """

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"OpenRouter circuit breaker open; retry after {retry_after_seconds}s"
        )


def _sync_client() -> Redis:
    """Sync Redis client for the worker-side surface, built the same way
    `workers/progress.py`'s `get_sync_redis()` builds its publisher client."""
    settings = get_settings()
    return Redis.from_url(settings.redis_url, decode_responses=True)


def _interpret(
    opened_at_raw: str | None, now: float, open_seconds: int
) -> tuple[BreakerState, int]:
    """Pure state derivation shared by every surface below, so `closed` /
    `open` / `half_open` are computed identically everywhere instead of
    drifting across the sync and async copies.

    No `opened_at` recorded at all means the breaker has never tripped (or
    was just closed) — `closed`. Once `opened_at` is set, elapsed time alone
    decides `open` vs `half_open`; there is no separate "half-open" flag to
    fall out of sync with the timestamp.
    """
    if opened_at_raw is None:
        return "closed", 0
    elapsed = now - float(opened_at_raw)
    if elapsed < open_seconds:
        return "open", max(1, math.ceil(open_seconds - elapsed))
    return "half_open", 0


# ---- sync (worker) surface ---------------------------------------------------


def state() -> tuple[BreakerState, int]:
    """Current breaker state + `retry_after_seconds`. Redis errors degrade to
    `("closed", 0)` — see module docstring."""
    settings = get_settings()
    client = _sync_client()
    try:
        opened_at = client.get(_OPENED_AT_KEY)
    except Exception:  # noqa: BLE001 — fail-open, see module docstring
        log.warning(
            "circuit_breaker: state read failed, degrading to closed", exc_info=True
        )
        return "closed", 0
    finally:
        client.close()
    return _interpret(opened_at, time.time(), settings.openrouter_breaker_open_seconds)


def record_failure() -> None:
    """Count one failed OpenRouter call against the breaker.

    `closed`: increments the consecutive-failure counter; trips the breaker
    (sets `opened_at`) once it reaches the threshold.
    `half_open`: this failure is necessarily the probe's — re-open for a
    fresh full window and free the probe slot for the next window.
    `open`: no-op. A failure arriving while already open is almost always a
    call that started before the breaker tripped and is only now finishing;
    it doesn't need to (and shouldn't) push the open window further out —
    the window that's already running was sized for exactly this policy.
    """
    settings = get_settings()
    client = _sync_client()
    try:
        current_state, _ = _interpret(
            client.get(_OPENED_AT_KEY),
            time.time(),
            settings.openrouter_breaker_open_seconds,
        )
        if current_state == "open":
            return
        if current_state == "half_open":
            client.set(_OPENED_AT_KEY, str(time.time()))
            client.delete(_PROBE_KEY)
            return
        failures = client.incr(_FAILURES_KEY)
        if failures >= settings.openrouter_breaker_failure_threshold:
            client.set(_OPENED_AT_KEY, str(time.time()))
    except Exception:  # noqa: BLE001 — fail-open, see module docstring
        log.warning(
            "circuit_breaker: record_failure failed, degrading to closed "
            "(no trip recorded)",
            exc_info=True,
        )
    finally:
        client.close()


def record_success() -> None:
    """Count one successful OpenRouter call against the breaker.

    `half_open`: this success is necessarily the probe's — close the breaker
    fully (clear the failure count, the open timestamp, and the probe slot).
    `closed`: resets the consecutive-failure counter to zero.
    `open`: no-op. Only a probe success may close the breaker; a stray
    success while `open` (a call that started before the trip) must not
    mask a real outage by closing the breaker early.
    """
    settings = get_settings()
    client = _sync_client()
    try:
        current_state, _ = _interpret(
            client.get(_OPENED_AT_KEY),
            time.time(),
            settings.openrouter_breaker_open_seconds,
        )
        if current_state == "half_open":
            client.delete(_OPENED_AT_KEY)
            client.delete(_FAILURES_KEY)
            client.delete(_PROBE_KEY)
            return
        if current_state == "closed":
            client.delete(_FAILURES_KEY)
    except Exception:  # noqa: BLE001 — fail-open, see module docstring
        log.warning(
            "circuit_breaker: record_success failed, degrading to closed "
            "(state unchanged)",
            exc_info=True,
        )
    finally:
        client.close()


def precheck() -> None:
    """Call before every OpenRouter attempt (wired as `PrecheckHook`).

    `closed`: returns immediately. `open`: raises `CircuitOpenError` without
    touching Redis further. `half_open`: attempts to claim the probe slot
    with `SET NX EX` — the atomicity of `NX` is what guarantees exactly one
    of any number of concurrent callers wins; every loser raises
    `CircuitOpenError` instead of also calling out to OpenRouter. The probe
    key's TTL (the same as the open window) is a safety net only, in case
    the winning caller crashes before it can call `record_success` /
    `record_failure` — it is normally cleared explicitly by whichever of
    those two runs.
    """
    settings = get_settings()
    client = _sync_client()
    try:
        opened_at = client.get(_OPENED_AT_KEY)
        current_state, retry_after = _interpret(
            opened_at, time.time(), settings.openrouter_breaker_open_seconds
        )
        if current_state == "closed":
            return
        if current_state == "open":
            raise CircuitOpenError(retry_after)
        won_probe = client.set(
            _PROBE_KEY, "1", nx=True, ex=settings.openrouter_breaker_open_seconds
        )
        if not won_probe:
            raise CircuitOpenError(0)
        return
    except CircuitOpenError:
        raise
    except Exception:  # noqa: BLE001 — fail-open, see module docstring
        log.warning(
            "circuit_breaker: precheck failed, degrading to closed "
            "(call allowed through)",
            exc_info=True,
        )
        return
    finally:
        client.close()


def on_failure(exc: BaseException) -> None:
    """Call after an OpenRouter call finally fails (wired as
    `OnCallFailureHook`). Only exceptions `is_breaker_failure()` deems
    outage-shaped (connection/timeout/rate-limit/5xx) count — a plain 4xx
    is a bad request, not evidence OpenRouter is down, and must not trip
    the breaker."""
    if not is_breaker_failure(exc):
        return
    record_failure()


# ---- async (API) surface -----------------------------------------------------


async def assert_closed() -> None:
    """Gate for the job-submit routers: raise 503 while the breaker is
    `open`. Does its own async Redis round-trip rather than reusing the sync
    `state()` above — a sync Redis call would block the event loop, which
    every other async path in this codebase (see `redis_client.py`,
    `idempotency.py`) avoids. See module docstring for why `half_open` is
    allowed through here (the single-probe admission happens in
    `precheck()`, not here).
    """
    settings = get_settings()
    redis = get_redis_client()
    try:
        opened_at = await redis.get(_OPENED_AT_KEY)
    except Exception:  # noqa: BLE001 — fail-open, see module docstring
        log.warning(
            "circuit_breaker: assert_closed check failed, degrading to closed "
            "(submission allowed)",
            exc_info=True,
        )
        return
    finally:
        await redis.aclose()

    current_state, retry_after = _interpret(
        opened_at, time.time(), settings.openrouter_breaker_open_seconds
    )
    if current_state == "open":
        raise HTTPException(
            status_code=503,
            detail="OpenRouter is temporarily unavailable; try again shortly.",
            headers={"Retry-After": str(retry_after)},
        )


__all__ = [
    "BreakerState",
    "CircuitOpenError",
    "assert_closed",
    "on_failure",
    "precheck",
    "record_failure",
    "record_success",
    "state",
]
