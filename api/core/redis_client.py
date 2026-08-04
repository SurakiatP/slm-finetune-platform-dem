"""Async Redis client factory.

Used by:
  • the WebSocket endpoint to subscribe to `job:{job_id}` channels
  • services that publish progress (Phase 4+) and read job state
  • the last-frame snapshot store (`job:{job_id}:last`), written alongside
    every publish (`workers/progress.py`) and read on WS connect
    (`api/routers/websocket.py`) and by `GET /api/v1/jobs/{job_id}/progress`
    (`api/routers/jobs.py`)

Keep one client per request/connection — don't share across event loops.
"""

from __future__ import annotations

from redis.asyncio import Redis, from_url

from api.core.config import get_settings

JOB_SNAPSHOT_TTL_SECONDS = 86_400  # 24h


def get_redis_client() -> Redis:
    """Return a fresh async Redis client; caller is responsible for `close()`."""
    settings = get_settings()
    return from_url(settings.redis_url, decode_responses=True)


def job_channel(job_id: str) -> str:
    """Channel name pattern for per-job progress messages."""
    return f"job:{job_id}"


def job_snapshot_key(job_id: str) -> str:
    """Redis key holding the last-published progress frame for one job."""
    return f"job:{job_id}:last"


__all__ = [
    "JOB_SNAPSHOT_TTL_SECONDS",
    "get_redis_client",
    "job_channel",
    "job_snapshot_key",
]
