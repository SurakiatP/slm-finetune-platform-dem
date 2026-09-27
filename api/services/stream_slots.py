"""Redis-backed concurrent-SSE-stream limiter for `/inference/*` streaming.

A streaming inference call holds an open connection to Ollama (and to the
caller) for up to `inference_stream_max_seconds` — much longer than a
normal request — so, same motivation as `api/services/quota.py`'s job
quotas, both a per-actor and a global cap exist: `inference_stream_max_per_actor`
and `inference_stream_max_global`.

Unlike `quota.py`, there is no DB row to count here — a stream is a live
HTTP response, not a `Dataset`/`TrainingJob` entity — so the DB-is-truth
argument in `quota.py`'s module docstring does not apply, and Redis is the
only shared place both this process and any other API worker could agree
on a live count.

`quota.py`'s docstring specifically warns against a plain Redis
INCR-on-start/DECR-on-end counter: a worker SIGKILLed (or a stream that
ends via disconnect/crash without ever reaching its cleanup path) mid-flight
never runs the DECR, so the counter leaks upward forever and 429s every
future caller until someone manually resets it. This module avoids that
failure mode entirely by never counting at all — it stores one Redis ZSET
member per held slot, scored by its own absolute expiry timestamp
(`now + inference_stream_max_seconds + inference_stream_idle_timeout_seconds`,
i.e. comfortably past the longest this stream could legitimately still be
open), and every `acquire()` call — from *any* actor, since the global key
is shared — starts by purging every member whose score has passed (`ZREMRANGEBYSCORE
key -inf now`). A slot that leaked because its `release()` never ran is
therefore removed by the very next `acquire()` call to touch that key, with
no reconciliation sweep, no manual reset, and no separate process required
— it just stops counting once its own expiry passes.

Failure mode, same as `idempotency.py` / `circuit_breaker.py`: any Redis
error is logged and swallowed. `acquire()` degrades to `None` (fail open —
the stream proceeds uncapped rather than 500ing every inference call
because the limiter's backing store is down); `release()` degrades to
"do nothing" (the slot self-heals via its expiry score above; never worse
than the leaked-counter case this design exists to avoid, and always
better than raising out of a stream's cleanup path).
"""

from __future__ import annotations

import logging
import math
import time
import uuid

from fastapi import HTTPException

from api.core.config import get_settings
from api.core.redis_client import get_redis_client

log = logging.getLogger("api.stream_slots")

_ACTOR_KEY_PREFIX = "stream:slots:actor:"
_GLOBAL_KEY = "stream:slots:global"


def _actor_key(actor: str) -> str:
    return f"{_ACTOR_KEY_PREFIX}{actor}"


async def acquire(actor: str) -> str | None:
    """Reserve one concurrent-stream slot for `actor`.

    Returns a `slot_id` to hand back to `release()` when the stream ends.
    Raises 429 (with `Retry-After`) if `actor`'s or the global cap is
    already full. Returns `None` — never raises — if Redis itself is
    unavailable; see module docstring for why that means "let the stream
    through uncapped" rather than "fail the request".
    """
    settings = get_settings()
    slot_id = uuid.uuid4().hex
    now = time.time()
    horizon = (
        settings.inference_stream_max_seconds
        + settings.inference_stream_idle_timeout_seconds
    )
    expiry = now + horizon + 30
    # Key TTL outlives the score-based expiry with its own margin, so the
    # whole key never vanishes (and silently drop a still-valid member)
    # while a slot inside it could still legitimately be held.
    key_ttl = math.ceil(horizon + 60)
    actor_key = _actor_key(actor)

    redis = get_redis_client()
    try:
        pipe = redis.pipeline(transaction=True)
        pipe.zremrangebyscore(actor_key, "-inf", now)
        pipe.zadd(actor_key, {slot_id: expiry})
        pipe.zcard(actor_key)
        pipe.expire(actor_key, key_ttl)
        pipe.zremrangebyscore(_GLOBAL_KEY, "-inf", now)
        pipe.zadd(_GLOBAL_KEY, {slot_id: expiry})
        pipe.zcard(_GLOBAL_KEY)
        pipe.expire(_GLOBAL_KEY, key_ttl)
        results = await pipe.execute()
    except Exception:  # fail-open, see module docstring
        log.warning("stream_slots: acquire failed, degrading to unlimited", exc_info=True)
        return None
    finally:
        await redis.aclose()

    actor_count = results[2]
    global_count = results[6]

    # Same precedence as `quota.py.assert_can_submit`: global cap checked
    # first, per-actor cap second.
    if global_count > settings.inference_stream_max_global:
        await release(actor, slot_id)
        raise HTTPException(
            status_code=429,
            detail=(
                "Too many concurrent streams "
                f"(global limit {settings.inference_stream_max_global}). "
                "Try again shortly."
            ),
            headers={"Retry-After": str(settings.quota_retry_after_seconds)},
        )
    if actor_count > settings.inference_stream_max_per_actor:
        await release(actor, slot_id)
        raise HTTPException(
            status_code=429,
            detail=(
                "Too many concurrent streams "
                f"(limit {settings.inference_stream_max_per_actor} per caller). "
                "Try again shortly."
            ),
            headers={"Retry-After": str(settings.quota_retry_after_seconds)},
        )
    return slot_id


async def release(actor: str, slot_id: str | None) -> None:
    """Free a slot reserved by `acquire()`.

    No-op when `slot_id is None` (nothing was ever reserved — e.g. Redis
    was already down at acquire time). Never raises: a failed release is
    not fatal, the slot's own expiry score purges it on a later `acquire()`
    regardless (see module docstring).
    """
    if slot_id is None:
        return
    redis = get_redis_client()
    try:
        pipe = redis.pipeline(transaction=True)
        pipe.zrem(_actor_key(actor), slot_id)
        pipe.zrem(_GLOBAL_KEY, slot_id)
        await pipe.execute()
    except Exception:  # fail-open, see module docstring
        log.warning(
            "stream_slots: release failed, slot will self-heal via its expiry score",
            exc_info=True,
        )
    finally:
        await redis.aclose()


__all__ = ["acquire", "release"]
