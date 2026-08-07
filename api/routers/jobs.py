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

**Two different failures, two different codes (ADR-012).** `job_id` unknown
to `resolve_job_owner` (no Dataset/TrainingJob/EvaluationRun/ModelArtifact
anywhere references it) is a genuine 404 — same as "no frame yet", "TTL
lapsed" and "corrupt payload" below, which are all "there is nothing here"
in the same sense. A `job_id` that *does* resolve, but to a project owned by
someone else (or by nobody, `owner_id IS NULL`, which fails closed the same
way) is a 403 — the job demonstrably exists, the caller just isn't allowed
to see it. This mirrors `api/services/ownership.py`'s `assert_*_access`
split exactly, and inherits the same accepted trade-off: a 403 here tells an
authenticated caller that a given `job_id` belongs to *someone*, which a
uniform 404 would not. See `ownership.py`'s module docstring and ADR-012 for
why that trade was made anyway.

Once past the ownership check (or when `user is None` and it's skipped
entirely), "no frame yet" / "TTL lapsed" / "corrupt payload" are unrelated to
ownership and all still collapse into the same 404 they always have — there
is no separate id to leak on that path, only whether a Redis key happens to
be populated right now.

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
    """There is nothing here — shared by the no-frame, corrupt-frame and
    unknown-job paths, none of which have a real id to be 403 about.
    """
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"No progress frame for job {job_id}",
    )


def _forbidden(job_id: str) -> HTTPException:
    """The job resolves to a real row; `user` just doesn't own it (or the
    row's `Project.owner_id` is null, which fails closed the same way — see
    `ownership.py`'s module docstring). ADR-012.
    """
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Job {job_id} is not accessible",
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
        if not owner.found:
            raise _not_found(job_id)
        # `owner.owner_id != user.id` also covers `owner_id is None` — a
        # Project created before auth is owned by nobody, not by everybody
        # (see `Project.owner_id`'s docstring). Fails closed, now as 403:
        # the job genuinely exists, `user` just isn't its owner (ADR-012).
        if owner.owner_id != user.id:
            raise _forbidden(job_id)

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
