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

    **For an authenticated caller** this is the exact aggregate
    `usage_service.assert_within_budget` checks against for the per-actor
    monthly cap — the same number that starts turning into 402s once it
    reaches `budget_monthly_usd_per_actor`.

    **For an anonymous caller it is not.** Actor resolution is `user.id`
    when a verified token is present and `None` otherwise (phase-1 /
    `AUTH_REQUIRED=false`), and a `None` actor rolls up only the rows
    recorded with no actor attached (`UsageEvent.actor_id IS NULL`). The
    budget gate, meanwhile, skips the per-actor cap entirely for such a
    caller and applies the **global** cap — so an anonymous caller can be
    402'd by a number this endpoint never showed them. Returning the global
    total here instead would make the two agree, but at the price of leaking
    every other user's spend to anyone unauthenticated, which is not a
    trade worth making.

    The endpoint sits under `require_user`, so it never itself 401s an
    anonymous caller while `AUTH_REQUIRED=false`.
    """
    actor_id = user.id if user is not None else None
    return await usage_service.summary_for_actor(
        db,
        actor_id=actor_id,
        since=usage_service._current_month_start(),
    )
