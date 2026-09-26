"""Analytics router -- owner-scoped job/usage rollup for the frontend's
`/analytics` page.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.auth import CurrentUser, require_user
from api.core.database import get_db
from api.schemas.analytics import AnalyticsResponse
from api.services import analytics_service

router = APIRouter()


@router.get(
    "",
    response_model=AnalyticsResponse,
    summary="Owner-scoped job/usage analytics rollup for a date window",
)
async def get_analytics(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    project_id: Annotated[UUID | None, Query()] = None,
) -> AnalyticsResponse:
    """Job counts/timings (SDG generation, training, evaluation) and
    OpenRouter token/cost usage for the caller's own resources over
    `[from, to]` -- both inclusive UTC calendar dates, defaulting to the
    last 30 days ending today when omitted. 422 if `from` is after `to` or
    the span exceeds 366 days.

    `project_id`, if given, narrows every query to that project and is
    itself ownership-checked first (`ownership.assert_project_access`) --
    403 for a project that exists but isn't the caller's, 404 if it
    doesn't exist at all, same as every other project-scoped endpoint.

    An authenticated caller only ever sees their own jobs and spend: job
    counts go through `ownership.scope_*_to_owner` and usage/cost through
    the actor-id convention `usage_service.summary_for_actor` established.
    An anonymous caller (phase-1, `AUTH_REQUIRED=false` only -- production
    refuses to boot that way) gets what those helpers give anonymous
    callers everywhere else: unfiltered job counts, but only the
    `actor_id IS NULL` usage bucket for tokens/cost.
    """
    return await analytics_service.get_analytics(
        db, user=user, date_from=date_from, date_to=date_to, project_id=project_id
    )
