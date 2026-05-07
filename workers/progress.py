"""Sync Redis publisher used by Celery tasks to push WS messages.

The async equivalent lives in `api/core/redis_client.py` (used by the
WebSocket subscriber).
"""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator

from pydantic import BaseModel
from redis import Redis

from api.core.config import get_settings
from api.core.redis_client import job_channel


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
    """Serialize a `WSMessage` (Pydantic) and publish to `job:{job_id}`."""
    client.publish(job_channel(job_id), message.model_dump_json())


__all__ = ["get_sync_redis", "sync_redis_scope", "publish_ws_message"]
