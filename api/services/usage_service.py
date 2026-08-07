"""Usage event writes + aggregates + the monthly budget gate.

`record()` (and `record_run()`, its batch form) is the single write path
for every `UsageEvent` row across the codebase — both the async API and
the sync Celery workers call it. Both `sqlalchemy.orm.Session.add()` and
`sqlalchemy.ext.asyncio.AsyncSession.add()` are synchronous methods (only
`flush`/`commit`/`execute`/etc. need `await`), which is exactly what lets
one helper serve both call sites without an async/sync split — same
reasoning as `api/services/audit_service.py`, which this module mirrors
deliberately.

`api.core.auth` (and `api.services.ownership`, which imports it) pulls in
PyJWT at import time. The Celery workers import *this* module for
`record()`/`record_run()` alone — the write path, which has no notion of
a request or a token — via `workers/tasks/data_generation.py`, and the GPU
worker image does not ship PyJWT. Importing either eagerly, even purely
for a type annotation, turns a harmless-looking `CurrentUser` reference
into a hard `ModuleNotFoundError: No module named 'jwt'` that crash-loops
every worker on boot (this exact bug already took down the worker fleet
once — see `tests/unit/test_worker_import_surface.py`, which guards
against it recurring). So, exactly as `audit_service.py` does:

  * `CurrentUser` is imported only under `if TYPE_CHECKING:`.
  * `from api.services import ownership` is imported *inside*
    `list_project_usage`, the one function that needs it, never at module
    scope.

Everything else in this module (the write path, the aggregates, the
budget gate) imports only `sqlalchemy`, `api.models.usage_event`,
`api.core.config`, and `api.services.model_pricing` — none of which touch
PyJWT.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import Select, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ai_engine.data_gen.usage import UsageEntry
from api.core.config import get_settings
from api.models.usage_event import UsageEvent
from api.schemas.responses import Page
from api.schemas.usage import UsageEventResponse, UsageRollupItem, UsageSummaryResponse
from api.services import model_pricing

if TYPE_CHECKING:  # pragma: no cover - typing only
    from api.core.auth import CurrentUser


class BudgetExceeded(RuntimeError):
    """Raised by the sync (worker-callable) budget check.

    A plain exception, not an `HTTPException`: the Celery worker has no
    FastAPI error-handling middleware to catch an `HTTPException` and turn
    it into a response, so raising one there would just be an odd-looking
    `RuntimeError` subclass with unused HTTP semantics. The async API path
    (`assert_within_budget`) raises `HTTPException(402, ...)` instead,
    which *is* meaningful there.
    """

    def __init__(self, scope: str, spent_usd: Decimal, limit_usd: float) -> None:
        self.scope = scope
        self.spent_usd = spent_usd
        self.limit_usd = limit_usd
        super().__init__(
            f"{scope} monthly budget exceeded: spent ${spent_usd} of "
            f"${limit_usd:.2f} limit"
        )


# =============================================================================
# Write path
# =============================================================================


def record(
    session: Session | AsyncSession,
    *,
    actor_id: str | None,
    project_id: UUID | None,
    job_id: str | None,
    provider: str,
    model: str,
    stage: str,
    prompt_tokens: int,
    completion_tokens: int,
    outcome: str,
) -> UsageEvent:
    """Build a `UsageEvent` (resolving `cost_usd` via `model_pricing`) and
    `session.add()` it. That's all.

    Deliberately no `commit`/`flush` and no `try/except` — same contract,
    and same reason, as `audit_service.record`: this call rides inside the
    caller's own transaction, and a failing insert must propagate and fail
    that transaction rather than be swallowed here.
    """
    cost = model_pricing.cost_usd(model, prompt_tokens, completion_tokens)
    event = UsageEvent(
        actor_id=actor_id,
        project_id=project_id,
        job_id=job_id,
        provider=provider,
        model=model,
        stage=stage,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost,
        outcome=outcome,
    )
    session.add(event)
    return event


def record_run(
    session: Session | AsyncSession,
    entries: Iterable[UsageEntry],
    *,
    actor_id: str | None,
    project_id: UUID | None,
    job_id: str | None,
    outcome: str,
    provider: str = "openrouter",
) -> list[UsageEvent]:
    """`record()` one row per `(model, stage)` bucket in `entries`.

    `entries` is expected to be the output of
    `ai_engine.data_gen.usage.UsageAccumulator.entries()` — already merged
    per (model, stage), so this is a thin loop, not its own aggregation.
    """
    return [
        record(
            session,
            actor_id=actor_id,
            project_id=project_id,
            job_id=job_id,
            provider=provider,
            model=entry.model,
            stage=entry.stage,
            prompt_tokens=entry.prompt_tokens,
            completion_tokens=entry.completion_tokens,
            outcome=outcome,
        )
        for entry in entries
    ]


# =============================================================================
# Aggregates
# =============================================================================


def _current_month_start() -> datetime:
    """Start of the current UTC calendar month — matches OpenRouter's own
    billing period, and is what both `assert_within_budget` and the
    `GET /usage` read endpoint anchor on.
    """
    now = datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _monthly_spend_stmt(*, actor_id: str | None) -> Select[tuple[Decimal | None]]:
    """The one query shape behind every "spend so far this month" number in
    the system: `GET /usage`'s summary, and the budget gate below, both
    read through this. `actor_id=None` means "no actor filter" — i.e. the
    global total — which is what lets `global_monthly_spend_usd` reuse
    this instead of hand-rolling a second, potentially-diverging query.

    `SUM(cost_usd)` silently skips NULL rows (SQL's normal aggregate
    behavior) — that is what makes the result a floor, not a total: usage
    against an unpriced model is in the table (tokens recorded) but never
    inflates, or deflates, this number.
    """
    start = _current_month_start()
    stmt = select(func.sum(UsageEvent.cost_usd)).where(UsageEvent.created_at >= start)
    if actor_id is not None:
        stmt = stmt.where(UsageEvent.actor_id == actor_id)
    return stmt


def _to_decimal(value: Decimal | float | int | None) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    # sqlite's SUM() over a NUMERIC column can hand back a plain float
    # (unlike Postgres, which returns a Decimal natively) — route through
    # `str()` rather than `Decimal(float)` directly to avoid importing the
    # float's full binary-precision noise into the result.
    return Decimal(str(value))


async def monthly_spend_usd(session: AsyncSession, *, actor_id: str) -> Decimal:
    """This actor's `SUM(cost_usd)` for the current UTC calendar month,
    floored at 0. Async variant — used by the API request path.
    """
    result = await session.execute(_monthly_spend_stmt(actor_id=actor_id))
    return _to_decimal(result.scalar_one())


def monthly_spend_usd_sync(session: Session, *, actor_id: str) -> Decimal:
    """Sync twin of `monthly_spend_usd` — used by the Celery worker."""
    result = session.execute(_monthly_spend_stmt(actor_id=actor_id))
    return _to_decimal(result.scalar_one())


async def global_monthly_spend_usd(session: AsyncSession) -> Decimal:
    """`SUM(cost_usd)` across all actors for the current UTC calendar
    month, floored at 0. Async variant.
    """
    result = await session.execute(_monthly_spend_stmt(actor_id=None))
    return _to_decimal(result.scalar_one())


def global_monthly_spend_usd_sync(session: Session) -> Decimal:
    """Sync twin of `global_monthly_spend_usd` — used by the Celery worker."""
    result = session.execute(_monthly_spend_stmt(actor_id=None))
    return _to_decimal(result.scalar_one())


# =============================================================================
# Budget gate
# =============================================================================


async def assert_within_budget(db: AsyncSession, *, actor_id: str | None) -> None:
    """Raise `HTTPException(402)` if either budget cap is already met.

    Both `budget_monthly_usd_per_actor` and `budget_monthly_usd_global`
    default to `None` (unlimited) — a `None` limit is skipped entirely,
    not treated as 0. The per-actor check only runs when `actor_id` is not
    `None` (there is nothing to scope it to otherwise); the global cap
    always runs, including for an anonymous (`actor_id=None`) caller —
    an unauthenticated request still spends real OpenRouter dollars against
    the same account, so it must not be exempt from the one cap that can
    see it.

    "Over budget" means `spent >= limit`, not `>`, matching
    `ai_engine.data_gen.usage.UsageAccumulator.check_budget`'s documented
    choice: hitting the limit exactly still stops the next spend.
    """
    settings = get_settings()

    if actor_id is not None and settings.budget_monthly_usd_per_actor is not None:
        spent = await monthly_spend_usd(db, actor_id=actor_id)
        if spent >= Decimal(str(settings.budget_monthly_usd_per_actor)):
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=(
                    f"Monthly budget exceeded for this account: spent "
                    f"${spent} of ${settings.budget_monthly_usd_per_actor:.2f} limit"
                ),
            )

    if settings.budget_monthly_usd_global is not None:
        spent = await global_monthly_spend_usd(db)
        if spent >= Decimal(str(settings.budget_monthly_usd_global)):
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=(
                    f"Global monthly budget exceeded: spent ${spent} of "
                    f"${settings.budget_monthly_usd_global:.2f} limit"
                ),
            )


def assert_within_budget_sync(db: Session, *, actor_id: str | None) -> None:
    """Worker-callable twin of `assert_within_budget`.

    Raises `BudgetExceeded`, not `HTTPException` — the worker has no
    FastAPI exception-handling middleware, and must not depend on one.
    Same "`None` limit is unlimited" / "per-actor only when `actor_id` is
    set, global always" / ">=`" semantics as the async version above.
    """
    settings = get_settings()

    if actor_id is not None and settings.budget_monthly_usd_per_actor is not None:
        spent = monthly_spend_usd_sync(db, actor_id=actor_id)
        if spent >= Decimal(str(settings.budget_monthly_usd_per_actor)):
            raise BudgetExceeded("actor", spent, settings.budget_monthly_usd_per_actor)

    if settings.budget_monthly_usd_global is not None:
        spent = global_monthly_spend_usd_sync(db)
        if spent >= Decimal(str(settings.budget_monthly_usd_global)):
            raise BudgetExceeded("global", spent, settings.budget_monthly_usd_global)


# =============================================================================
# Read helpers (async only — API-side)
# =============================================================================


async def summary_for_actor(
    db: AsyncSession, *, actor_id: str, since: datetime
) -> UsageSummaryResponse:
    """This actor's cross-project usage/cost rollup since `since`, grouped
    by `(model, stage)`.

    `has_unpriced_usage` is computed from a dedicated `COUNT` of NULL-cost
    rows in the same window, not inferred from `items` — inferring it from
    "any item's `cost_usd` is None" would be correct too (an item's
    `SUM(cost_usd)` is NULL only when every row in that bucket is unpriced),
    but a direct count is more obviously correct and doesn't rely on
    SQL's NULL-skipping SUM behavior being remembered correctly by a future
    reader.
    """
    period_end = datetime.now(UTC)

    # `sum(case(...))` rather than `func.count().filter(...)` — the SQL
    # `FILTER (WHERE ...)` clause is not universally available across the
    # dialects this codebase might run tests/prod against, while
    # `CASE WHEN ... THEN 1 ELSE 0 END` is plain ANSI SQL every backend
    # (sqlite included) understands identically.
    totals_stmt = select(
        func.coalesce(func.sum(UsageEvent.prompt_tokens), 0),
        func.coalesce(func.sum(UsageEvent.completion_tokens), 0),
        func.sum(UsageEvent.cost_usd),
        func.sum(case((UsageEvent.cost_usd.is_(None), 1), else_=0)),
    ).where(UsageEvent.actor_id == actor_id, UsageEvent.created_at >= since)

    total_prompt, total_completion, total_cost, unpriced_rows = (
        await db.execute(totals_stmt)
    ).one()

    items_stmt = (
        select(
            UsageEvent.model,
            UsageEvent.stage,
            func.sum(UsageEvent.prompt_tokens),
            func.sum(UsageEvent.completion_tokens),
            func.sum(UsageEvent.cost_usd),
        )
        .where(UsageEvent.actor_id == actor_id, UsageEvent.created_at >= since)
        .group_by(UsageEvent.model, UsageEvent.stage)
    )
    rows = (await db.execute(items_stmt)).all()

    items = [
        UsageRollupItem(
            model=model,
            stage=stage,
            prompt_tokens=int(prompt_sum or 0),
            completion_tokens=int(completion_sum or 0),
            cost_usd=_to_decimal(cost_sum) if cost_sum is not None else None,
        )
        for model, stage, prompt_sum, completion_sum, cost_sum in rows
    ]

    return UsageSummaryResponse(
        period_start=since,
        period_end=period_end,
        prompt_tokens=int(total_prompt or 0),
        completion_tokens=int(total_completion or 0),
        cost_usd=_to_decimal(total_cost) if total_cost is not None else None,
        items=items,
        has_unpriced_usage=bool(unpriced_rows),
    )


async def list_project_usage(
    db: AsyncSession,
    project_id: UUID,
    *,
    limit: int,
    offset: int,
    user: CurrentUser | None,
) -> Page[UsageEventResponse]:
    """Paginated usage log for one project, newest first.

    `assert_project_access` runs first — before touching `usage_events` at
    all — so a non-owner gets the same 404 they'd get probing the project
    directly, rather than an empty (and therefore existence-revealing)
    page. Same reasoning as `audit_service.list_activity`, which this
    mirrors.
    """
    from api.services import ownership  # deferred — see the module header

    await ownership.assert_project_access(db, project_id, user)

    base = (
        select(UsageEvent)
        .where(UsageEvent.project_id == project_id)
        .order_by(UsageEvent.created_at.desc(), UsageEvent.id.desc())
    )
    count = select(func.count()).select_from(UsageEvent).where(
        UsageEvent.project_id == project_id
    )

    total = int((await db.execute(count)).scalar_one())
    rows = (await db.execute(base.limit(limit).offset(offset))).scalars().all()

    return Page[UsageEventResponse](
        items=[UsageEventResponse.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


__all__ = [
    "BudgetExceeded",
    "assert_within_budget",
    "assert_within_budget_sync",
    "global_monthly_spend_usd",
    "global_monthly_spend_usd_sync",
    "list_project_usage",
    "monthly_spend_usd",
    "monthly_spend_usd_sync",
    "record",
    "record_run",
    "summary_for_actor",
]
