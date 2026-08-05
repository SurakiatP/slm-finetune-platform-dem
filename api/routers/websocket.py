"""WebSocket job-progress endpoint.

Workers publish JSON messages (matching `api.schemas.progress.WSMessage`) to
the Redis channel `job:{job_id}`. This endpoint subscribes to that channel
on connect and forwards messages verbatim until either:
  • the client disconnects, or
  • the Pub/Sub stream errors out.

Snapshot-on-connect: once `subscribe()` succeeds, the endpoint reads the
last-published frame for this job from `job:{job_id}:last` (written by
`workers/progress.py::publish_ws_message`, TTL 24h) and sends it immediately,
before any live frames. So a freshly opened or reloaded connection sees the
job's most recent known state right away instead of waiting on the next
publish — the same snapshot is also available over REST via
`GET /api/v1/jobs/{job_id}/progress`. Authorization (below) always runs
*before* this — the snapshot-on-connect behaviour itself (ADR-008) is
otherwise untouched.

Accepted race: a frame published between `subscribe()` and the snapshot
`GET` is delivered twice (once via the snapshot read, once via the live
relay). This is harmless — every frame is a full state snapshot, not a
delta, and consumers keep only the latest one they've seen, so duplicates
are simply redundant, not incorrect.

## Authorization

Browsers cannot set headers on `new WebSocket()`, so the credential travels
as the handshake's `Sec-WebSocket-Protocol` instead, offered as two values:
`new WebSocket(url, ["bearer", "<jwt>"])`. That keeps the token out of the
URL, nginx access logs and browser history, which a `?token=` query param
would not. If we accept the connection, we echo the selected subprotocol
back (`ws.accept(subprotocol="bearer")`) — omitting that makes the browser
close the connection immediately, since it thinks negotiation failed.

Only **nothing offered at all** counts as "no credential". A client that
offers *some* subprotocol but not the exact two-value `["bearer", "<jwt>"]`
shape (one value, more than two, or a first value that isn't "bearer") is
rejected in both phases — it is a caller error, not anonymity, and nothing
else in this system negotiates a subprotocol. This is deliberately stricter
than `api.core.auth.extract_bearer_token`, which folds a malformed
`Authorization` header into "absent": a header can arrive from proxies and
tooling we don't control, whereas a WebSocket subprotocol is only ever set
by our own client code.

Close codes (application range, mirroring HTTP for readability):
  • **4401** — no credential offered while `settings.auth_required` is
    True, or a credential WAS offered but failed verification (bad
    signature/expired/wrong audience/wrong issuer/malformed shape). Applies
    in both rollout phases per `api/core/auth.py`'s rule: "optional"
    tolerates absence, not garbage.
  • **4403** — credential verified, but `job_id` is unknown OR belongs to a
    different user. These two cases are made **indistinguishable** on
    purpose (same code, same reason) — otherwise the endpoint becomes an
    oracle a caller could probe to enumerate which job ids exist, exactly
    the "Celery UUID as a secret" anti-pattern `BACKEND_GAP_ANALYSIS.md`
    calls out.
  • Authorization always happens **before** `ws.accept()` — rejection is a
    `ws.close()` on the still-un-accepted socket, never an accept-then-hang-up.

**Phase-1 compatibility** (`settings.auth_required=False`, the default):
when the client offers *no* subprotocol at all, the connection is accepted
and streamed exactly as before this change — zero behavioural difference,
so the existing (pre-auth) test suite and the current `smart-model-tune`
frontend, which doesn't send a subprotocol yet, are both unaffected. A
subprotocol that IS offered is still fully verified and ownership-checked
regardless of `auth_required`, mirroring how `current_user_optional` treats
a present-but-invalid `Authorization` header as a hard 401 in both phases.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketState

from api.core.auth import CurrentUser, verify_supabase_jwt
from api.core.config import get_settings
from api.core.database import get_db
from api.core.redis_client import get_redis_client, job_channel, job_snapshot_key
from api.services.job_ownership import resolve_job_owner

router = APIRouter()
log = logging.getLogger(__name__)

# Application-range WebSocket close codes (4000-4999). See the module
# docstring's "Authorization" section for exactly what each one means and
# why unknown-vs-forbidden share one code.
_CLOSE_UNAUTHENTICATED = 4401
_CLOSE_FORBIDDEN_JOB = 4403


def _extract_ws_bearer_token(subprotocols: Sequence[str]) -> str | None:
    """Pull the bearer token out of the offered WS subprotocols, if present.

    Returns the token only for the exact two-value `["bearer", "<jwt>"]`
    shape (case-insensitive scheme, non-empty token); `None` for anything
    else.

    `None` here does **not** mean "anonymous" on its own — the caller
    distinguishes *nothing offered* (anonymous in phase 1) from *something
    offered that wasn't a valid credential* (rejected in both phases) by
    checking whether the subprotocol list was empty. See `_authorize` and
    the module docstring; conflating the two would let a client bypass
    authentication in phase 2 by offering a deliberately malformed
    subprotocol.
    """
    if len(subprotocols) != 2 or subprotocols[0].lower() != "bearer":
        return None
    token = subprotocols[1].strip()
    return token or None


async def _authorize(ws: WebSocket, job_id: str, db: AsyncSession) -> tuple[bool, str | None]:
    """Authorize the handshake before it is accepted.

    Returns `(True, subprotocol)` — proceed to `ws.accept(subprotocol=subprotocol)`
    — or `(False, None)`, meaning the socket has already been closed with the
    appropriate code and the caller must not accept or otherwise touch it
    further.
    """
    subprotocols = list(ws.scope.get("subprotocols") or [])
    offered = bool(subprotocols)
    token = _extract_ws_bearer_token(subprotocols)

    settings = get_settings()
    user: CurrentUser | None = None
    if token is not None:
        try:
            # Synchronous and, on a cold JWKS cache or rotated `kid`, blocks
            # on network IO — offload exactly like `current_user_optional`.
            user = await asyncio.to_thread(verify_supabase_jwt, token)
        except HTTPException:
            await ws.close(code=_CLOSE_UNAUTHENTICATED, reason="invalid authentication token")
            return False, None
    elif offered:
        # Something was offered but not the ["bearer", "<jwt>"] shape we
        # speak — not "absent", a caller error. Reject in both phases.
        await ws.close(code=_CLOSE_UNAUTHENTICATED, reason="malformed bearer credential")
        return False, None
    elif settings.auth_required:
        # Phase 2: nothing offered, and anonymous access is no longer allowed.
        await ws.close(code=_CLOSE_UNAUTHENTICATED, reason="authentication required")
        return False, None
    # else: phase 1, nothing offered at all -> anonymous pass-through,
    # identical to this endpoint's pre-auth behaviour.

    if user is not None:
        owner = await resolve_job_owner(db, job_id)
        # `owner.owner_id != user.id` also covers `owner.owner_id is None`
        # (a legacy pre-auth Project) — fails closed per Project.owner_id's
        # own documented rule, never "null owner = public".
        if not owner.found or owner.owner_id != user.id:
            await ws.close(code=_CLOSE_FORBIDDEN_JOB, reason="job not found")
            return False, None

    return True, ("bearer" if offered else None)


@router.websocket("/ws/jobs/{job_id}")
async def job_progress(ws: WebSocket, job_id: str, db: AsyncSession = Depends(get_db)) -> None:
    """Stream progress messages for one Celery job to the connected client."""
    proceed, subprotocol = await _authorize(ws, job_id, db)
    if not proceed:
        return
    await ws.accept(subprotocol=subprotocol)
    redis = get_redis_client()
    pubsub = redis.pubsub()
    channel = job_channel(job_id)

    async def _relay_redis_to_ws() -> None:
        async for msg in pubsub.listen():
            if msg.get("type") != "message":
                continue
            data = msg.get("data")
            if data is None:
                continue
            if isinstance(data, bytes):  # decode_responses=True should give str, but be defensive
                data = data.decode("utf-8", errors="replace")
            if ws.client_state != WebSocketState.CONNECTED:
                return
            await ws.send_text(data)

    async def _watch_client_close() -> None:
        try:
            # The client isn't expected to send anything; this just blocks until
            # the socket is closed, which raises WebSocketDisconnect.
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            return

    try:
        await pubsub.subscribe(channel)
    except RedisError as exc:
        log.exception("redis subscribe failed for %s", channel)
        await ws.close(code=1011, reason=f"redis error: {exc}")
        await pubsub.aclose()
        await redis.aclose()
        return

    try:
        snapshot = await redis.get(job_snapshot_key(job_id))
        if snapshot and ws.client_state == WebSocketState.CONNECTED:
            if isinstance(snapshot, bytes):  # decode_responses=True should give str, but be defensive
                snapshot = snapshot.decode("utf-8", errors="replace")
            await ws.send_text(snapshot)
    except RedisError:
        log.warning("redis snapshot read failed for %s", channel)

    relay_task = asyncio.create_task(_relay_redis_to_ws(), name=f"ws-relay-{job_id}")
    watch_task = asyncio.create_task(_watch_client_close(), name=f"ws-watch-{job_id}")
    try:
        _, pending = await asyncio.wait(
            {relay_task, watch_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    finally:
        try:
            await pubsub.unsubscribe(channel)
        except RedisError:
            log.warning("redis unsubscribe failed for %s", channel)
        await pubsub.aclose()
        await redis.aclose()
        if ws.client_state == WebSocketState.CONNECTED:
            await ws.close()
