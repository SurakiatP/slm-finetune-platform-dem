"""Job progress router — REST snapshot of the last WS frame for a job.

Backed by the same `job:{job_id}:last` Redis key that
`workers/progress.py::publish_ws_message` writes and
`api/routers/websocket.py` sends on connect. Gives non-WS clients (or a WS
client's first paint) an instant snapshot instead of waiting on the next
live frame.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import TypeAdapter, ValidationError

from api.core.redis_client import get_redis_client, job_snapshot_key
from api.schemas.progress import WSMessage

router = APIRouter()
log = logging.getLogger(__name__)

_snapshot_adapter: TypeAdapter[WSMessage] = TypeAdapter(WSMessage)


@router.get(
    "/{job_id}/progress",
    response_model=WSMessage,
    summary="Latest progress frame for a job (snapshot)",
)
async def get_job_progress(job_id: str) -> WSMessage:
    redis = get_redis_client()
    try:
        payload = await redis.get(job_snapshot_key(job_id))
    finally:
        await redis.aclose()

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No progress frame for job {job_id}",
        )

    try:
        return _snapshot_adapter.validate_json(payload)
    except ValidationError:
        log.exception("corrupt/legacy progress snapshot for job %s", job_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No progress frame for job {job_id}",
        ) from None


__all__ = ["router"]
