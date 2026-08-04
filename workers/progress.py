"""Sync Redis publisher used by Celery tasks to push WS messages.

The async equivalent lives in `api/core/redis_client.py` (used by the
WebSocket subscriber). Every publish also refreshes a last-frame snapshot
key (`job:{job_id}:last`, TTL 24h) so late/reconnecting subscribers and
`GET /api/v1/jobs/{job_id}/progress` can read the most recent state.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from collections.abc import Iterator

from pydantic import BaseModel
from redis import Redis
from redis.exceptions import RedisError

from api.core.config import get_settings
from api.core.redis_client import JOB_SNAPSHOT_TTL_SECONDS, job_channel, job_snapshot_key

log = logging.getLogger(__name__)


def get_sync_redis() -> Redis:
    settings = get_settings()
    return Redis.from_url(settings.redis_url, decode_responses=True)


@contextmanager
def sync_redis_scope() -> Iterator[Redis]:
    """Open one Redis connection for the duration of a task; close on exit."""
    client = get_sync_redis()
    try:
        yield client
    finally:
        client.close()


def publish_ws_message(client: Redis, job_id: str, message: BaseModel) -> None:
    """Serialize a `WSMessage` (Pydantic), snapshot it, then publish to `job:{job_id}`.

    Every frame type is stored, including terminal `JobCompleted` / `JobFailed`,
    so a client opening the page after the job finished immediately sees the
    final state via the snapshot key.

    The snapshot SET happens *before* the PUBLISH — a client that subscribes
    to the channel between these two operations then reads a populated
    snapshot key instead of an empty one. If the SET fails, that's logged and
    swallowed: a lost snapshot must never block or lose the live frame.
    """
    payload = message.model_dump_json()
    try:
        client.set(job_snapshot_key(job_id), payload, ex=JOB_SNAPSHOT_TTL_SECONDS)
    except RedisError:
        log.warning("failed to write progress snapshot for job %s", job_id, exc_info=True)
    client.publish(job_channel(job_id), payload)


__all__ = ["get_sync_redis", "sync_redis_scope", "publish_ws_message"]
