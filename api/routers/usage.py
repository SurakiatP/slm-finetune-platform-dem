"""Usage router — the caller's own cross-project usage/cost rollup."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.auth import CurrentUser, require_user
from api.core.database import get_db
from api.schemas.usage import UsageSummaryResponse
from api.services import usage_service

router = APIRouter()


@router.get(
    "",
    response_model=UsageSummaryResponse,
    summary="Caller's own usage/cost rollup for the current calendar month",
)
async def get_usage_summary(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
) -> UsageSummaryResponse:
    """The caller's cross-project usage/cost rollup since the start of the
    current UTC calendar month, grouped by (model, stage).

    This is the exact aggregate `usage_service.assert_within_budget` checks
    against for the per-actor monthly cap — a caller can read here the same
    number that will start turning into 402s once it reaches
    `budget_monthly_usd_per_actor`.

    Actor resolution matches every other actor-scoped call site in this
    codebase: `user.id` when a verified token is present, `None` otherwise
    (phase-1 / auth disabled) — same as `actor_id=request_context.
    current_user_id()` throughout `api/services/*_service.py`. A `None`
    actor is not an error here: it rolls up whatever usage was recorded
    with no actor attached (`UsageEvent.actor_id IS NULL`), which is the
    phase-1-correct behaviour — this endpoint is under `require_user`, so
    it never itself 401s an anonymous caller while `AUTH_REQUIRED=false`.
    """
    actor_id = user.id if user is not None else None
    return await usage_service.summary_for_actor(
        db,
        actor_id=actor_id,  # type: ignore[arg-type]  # None is valid: see docstring
        since=usage_service._current_month_start(),
    )
