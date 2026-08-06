"""Redis-backed 60-second dedupe window for job-submit endpoints.

A double-click (or an impatient retry) on a submit button must not enqueue
two Celery tasks for the same logical request — each one is a real OpenRouter
bill (SDG) or a real GPU job (training/export). This module gives the submit
routers a small, reusable pair of primitives to prevent that:

  * `replay()` — call first. If an identical request (same actor, same body,
    within the last `TTL_SECONDS`) already went through, return the *same*
    response body that was returned the first time, instead of enqueueing
    again.
  * `remember()` — call after a submission succeeds, to record the response
    so a subsequent duplicate within the window replays it.

Callers may also pass an explicit `Idempotency-Key` header to key the dedupe
on something other than the request body (e.g. a client-generated UUID),
which takes priority over the body hash.

This module is pure "is this a duplicate?" plumbing — no Celery, no
ai_engine, no knowledge of what a job even is. Wiring it into the actual
submit routers (SDG generate, training start, export start) is a separate
change; this file only owns the primitive.

Failure mode: Redis is a nice-to-have here, not a source of truth. Any Redis
error on either path is logged and swallowed — a broken Redis must degrade
to "no dedupe enforced", never turn a legitimate job submission into a 500.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from starlette.requests import Request

from api.core.auth import CurrentUser
from api.core.redis_client import get_redis_client

log = logging.getLogger("api.idempotency")

TTL_SECONDS = 60
REPLAY_HEADER = "X-Idempotent-Replay"
IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"


def canonical_json(body: Any) -> str:
    """Serialize `body` so that key order in the request never affects the hash."""
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def client_host(request: Request) -> str:
    """Best-effort caller address for anonymous dedupe bucketing.

    The agreed deployment topology is a single nginx serving the frontend
    same-origin, so `request.client.host` alone is always the proxy's own
    address — using it directly would collapse every anonymous caller into
    one shared dedupe bucket. `X-Forwarded-For`'s first hop is the actual
    client as seen by that proxy, so it's preferred when present; the raw
    socket address (or "unknown") is only a fallback for the case where
    something ends up talking to the app directly (e.g. tests, local dev).
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        first_hop = forwarded.split(",")[0].strip()
        if first_hop:
            return first_hop
    if request.client is not None:
        return request.client.host
    return "unknown"


def actor_for(request: Request, user: CurrentUser | None) -> str:
    """Identify the caller for dedupe purposes.

    Must produce a real bucket for `user is None` — the only real client
    today (`smart-model-tune`) sends no `Authorization` header yet, so
    skipping dedupe for anonymous callers would make this feature dead code
    for the exact caller it exists to protect.
    """
    if user is not None:
        return user.id
    return "anon:" + client_host(request)


def build_key(*, actor: str, path: str, body: Any, header_key: str | None) -> str:
    """Build the Redis key identifying one logical submission.

    An explicit `Idempotency-Key` header always wins over the body hash —
    that's the entire point of supporting it.
    """
    return f"idem:{actor}:{header_key or hashlib.sha256((path + canonical_json(body)).encode('utf-8')).hexdigest()}"


async def replay(
    request: Request, user: CurrentUser | None, body: Any
) -> JSONResponse | None:
    """Return the previously-stored response if this exact request was already made.

    Returns `None` on a miss, or if Redis itself is unavailable (dedupe is
    best-effort — never block a submission because Redis is down).
    """
    key = build_key(
        actor=actor_for(request, user),
        path=request.url.path,
        body=body,
        header_key=request.headers.get(IDEMPOTENCY_KEY_HEADER),
    )

    redis = get_redis_client()
    try:
        stored = await redis.get(key)
    except RedisError:
        log.warning("idempotency: replay lookup failed, degrading to no dedupe", exc_info=True)
        return None
    finally:
        await redis.aclose()

    if stored is None:
        return None

    return JSONResponse(
        status_code=202,
        content=json.loads(stored),
        headers={REPLAY_HEADER: "true"},
    )


async def remember(
    request: Request, user: CurrentUser | None, body: Any, payload: dict
) -> None:
    """Record `payload` as the response for this request, for `TTL_SECONDS`."""
    key = build_key(
        actor=actor_for(request, user),
        path=request.url.path,
        body=body,
        header_key=request.headers.get(IDEMPOTENCY_KEY_HEADER),
    )

    redis = get_redis_client()
    try:
        await redis.set(key, json.dumps(payload), ex=TTL_SECONDS)
    except RedisError:
        log.warning("idempotency: remember write failed, degrading to no dedupe", exc_info=True)
    finally:
        await redis.aclose()


__all__ = [
    "IDEMPOTENCY_KEY_HEADER",
    "REPLAY_HEADER",
    "TTL_SECONDS",
    "actor_for",
    "build_key",
    "canonical_json",
    "client_host",
    "remember",
    "replay",
]
