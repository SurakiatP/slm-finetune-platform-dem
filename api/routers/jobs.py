"""Job progress router — REST snapshot of the last WS frame for a job.

Backed by the same `job:{job_id}:last` Redis key that
`workers/progress.py::publish_ws_message` writes and
`api/routers/websocket.py` sends on connect. Gives non-WS clients (or a WS
client's first paint) an instant snapshot instead of waiting on the next
live frame.

## Authorization

This endpoint is the **REST twin of the `/ws/jobs/{job_id}` stream**, so it
carries the same ownership rule — protecting one and not the other would
leave the job-progress surface readable by anyone who can guess (or was once
handed) a Celery task id, which is exactly the "Celery UUID as a secret"
anti-pattern `BACKEND_GAP_ANALYSIS.md` rejects.

Every failure returns the *same* `404` with the *same* detail, whether the
job has published no frame yet, the snapshot TTL lapsed, the stored payload
is corrupt, the job id is unknown, or the job belongs to someone else. That
uniformity is deliberate: distinguishing them would turn this into an oracle
for enumerating other users' job ids. See ADR-009.

Phase 1 (`AUTH_REQUIRED=false`, no token presented): `user` is `None` and the
ownership check is skipped entirely — behaviour is identical to before auth
existed.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.auth import CurrentUser, require_user
from api.core.database import get_db
from api.core.redis_client import get_redis_client, job_snapshot_key
from api.schemas.progress import WSMessage
from api.services.job_ownership import resolve_job_owner

router = APIRouter()
log = logging.getLogger(__name__)

_snapshot_adapter: TypeAdapter[WSMessage] = TypeAdapter(WSMessage)


def _not_found(job_id: str) -> HTTPException:
    """The single failure response this endpoint ever gives.

    Shared by the no-frame, corrupt-frame, unknown-job and not-your-job
    paths so none of them can be told apart from the outside.
    """
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"No progress frame for job {job_id}",
    )


@router.get(
    "/{job_id}/progress",
    response_model=WSMessage,
    summary="Latest progress frame for a job (snapshot)",
)
async def get_job_progress(
    job_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
) -> WSMessage:
    if user is not None:
        owner = await resolve_job_owner(db, job_id)
        # `owner.owner_id != user.id` also covers `owner_id is None` — a
        # Project created before auth is owned by nobody, not by everybody
        # (see `Project.owner_id`'s docstring). Fails closed.
        if not owner.found or owner.owner_id != user.id:
            raise _not_found(job_id)

    redis = get_redis_client()
    try:
        payload = await redis.get(job_snapshot_key(job_id))
    finally:
        await redis.aclose()

    if not payload:
        raise _not_found(job_id)

    try:
        return _snapshot_adapter.validate_json(payload)
    except ValidationError:
        log.exception("corrupt/legacy progress snapshot for job %s", job_id)
        raise _not_found(job_id) from None


__all__ = ["router"]
