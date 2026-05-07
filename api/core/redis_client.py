"""Async Redis client factory.

Used by:
  • the WebSocket endpoint to subscribe to `job:{job_id}` channels
  • services that publish progress (Phase 4+) and read job state

Keep one client per request/connection — don't share across event loops.
"""

from __future__ import annotations

from redis.asyncio import Redis, from_url

from api.core.config import get_settings


def get_redis_client() -> Redis:
    """Return a fresh async Redis client; caller is responsible for `close()`."""
    settings = get_settings()
    return from_url(settings.redis_url, decode_responses=True)


def job_channel(job_id: str) -> str:
    """Channel name pattern for per-job progress messages."""
    return f"job:{job_id}"


__all__ = ["get_redis_client", "job_channel"]
