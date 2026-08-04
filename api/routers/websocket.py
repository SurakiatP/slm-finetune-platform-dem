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
`GET /api/v1/jobs/{job_id}/progress`.

Accepted race: a frame published between `subscribe()` and the snapshot
`GET` is delivered twice (once via the snapshot read, once via the live
relay). This is harmless — every frame is a full state snapshot, not a
delta, and consumers keep only the latest one they've seen, so duplicates
are simply redundant, not incorrect.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from redis.exceptions import RedisError
from starlette.websockets import WebSocketState

from api.core.redis_client import get_redis_client, job_channel, job_snapshot_key

router = APIRouter()
log = logging.getLogger(__name__)


@router.websocket("/ws/jobs/{job_id}")
async def job_progress(ws: WebSocket, job_id: str) -> None:
    """Stream progress messages for one Celery job to the connected client."""
    await ws.accept()
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
