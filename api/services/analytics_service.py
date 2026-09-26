"""Application service for `GET /api/v1/analytics` (the frontend's
`/analytics` page): an owner-scoped rollup of job counts/timings (SDG
generation, training, evaluation) and OpenRouter token/cost usage over a
date window, optionally narrowed to one project.

API-only module -- nothing in `workers/` imports this file (there is no
worker-side write path here, only reads), so it is not part of the
`_ROUND_2_SERVICE_MODULES` set `tests/unit/test_worker_import_surface.py`
guards. That means `api.services.ownership` (which pulls in `api.core.auth`
/ PyJWT) can be imported at plain module scope below -- the same way
`dataset_insights.py` does -- rather than deferred into each function the
way `usage_service.py`/`audit_service.py` must, since workers *do* import
those two.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import Select, case, extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.auth import CurrentUser
from api.models.dataset import Dataset
from api.models.evaluation_run import EvaluationRun
from api.models.model_artifact import ModelArtifact
from api.models.training_job import TrainingJob
from api.models.usage_event import UsageEvent
from api.schemas.analytics import (
    AnalyticsResponse,
    AnalyticsSeriesPoint,
    AnalyticsStage,
    AnalyticsTotals,
    StatusCounts,
)
from api.schemas.enums import DatasetSource, JobStatus
from api.services import ownership

# Width of the default window (last N days ending today) and the largest
# span the contract allows when both `from`/`to` are given explicitly.
_DEFAULT_WINDOW_DAYS = 30
_MAX_SPAN_DAYS = 366

_EPOCH_DATE = date(1970, 1, 1)


def _resolve_window(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    """Fill in the "last 30 days ending today (UTC)" default and enforce the
    two 422s the contract requires: `from` after `to`, or `to - from` over
    `_MAX_SPAN_DAYS` days (so at most `_MAX_SPAN_DAYS + 1` inclusive days,
    enough for any calendar year).
    """
    if date_to is None:
        date_to = datetime.now(UTC).date()
    if date_from is None:
        date_from = date_to - timedelta(days=_DEFAULT_WINDOW_DAYS - 1)
    if date_from > date_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="`from` must not be after `to`",
        )
    if (date_to - date_from).days > _MAX_SPAN_DAYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"date range must not exceed {_MAX_SPAN_DAYS} days",
        )
    return date_from, date_to


def _day_bucket(created_at_col):
    """Whole-UTC-calendar-day index (days since the Unix epoch) for a
    `DateTime(timezone=True)` column, computed portably across Postgres and
    sqlite.

    `extract("epoch", ...)` compiles to native `EXTRACT(EPOCH FROM ...)` on
    Postgres and to `STRFTIME('%s', ...)` on sqlite (SQLAlchemy's sqlite
    dialect implements `extract()` that way) -- both give absolute UTC
    seconds-since-epoch regardless of session/server timezone, verified
    against this repo's sqlite test DB. `floor()`, not `cast(..., Integer)`,
    is deliberate: Postgres rounds a float-to-integer cast to the *nearest*
    integer, which would bucket an afternoon timestamp into tomorrow --
    `FLOOR()` always truncates downward on both backends, which is what a
    calendar-day bucket needs.
    """
    return func.floor(extract("epoch", created_at_col) / 86400)


def _date_for_bucket(bucket: float | int) -> date:
    return _EPOCH_DATE + timedelta(days=int(bucket))


def _to_decimal(value: Decimal | float | int | None) -> Decimal | None:
    """Same float/Decimal normalization as `usage_service._to_decimal`
    (kept as a small local copy rather than importing that private helper
    across modules): sqlite's `SUM()`/`AVG()` over a NUMERIC column hands
    back a plain float where Postgres returns a native `Decimal`.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _status_counts(counts: dict[JobStatus, int]) -> StatusCounts:
    """Zero-fill every `JobStatus` value, not just the ones a query
    happened to return rows for."""
    return StatusCounts(**{s.value: counts.get(s, 0) for s in JobStatus})


def _usage_actor_filter(stmt: Select, user: CurrentUser | None) -> Select:
    """Mirrors `usage_service.summary_for_actor`'s convention exactly:
    `None` here means the anonymous bucket (`UsageEvent.actor_id IS NULL`),
    the opposite of `usage_service._monthly_spend_stmt`'s "no actor filter"
    `None`. Returning the platform-wide total to an unauthenticated caller
    would leak every other user's spend, which is exactly what this
    endpoint must never do.
    """
    actor_id = ownership.owner_id_for(user)
    if actor_id is None:
        return stmt.where(UsageEvent.actor_id.is_(None))
    return stmt.where(UsageEvent.actor_id == actor_id)


# =============================================================================
# Per-stage scoping -- one function per job source, each folding in the
# window, the matching `ownership.scope_*_to_owner` helper, and (if given)
# the project filter.
# =============================================================================


def _scope_sdg(
    stmt: Select,
    *,
    user: CurrentUser | None,
    project_id: UUID | None,
    start: datetime,
    end: datetime,
) -> Select:
    """SDG jobs = `Dataset` rows the generate pipeline produced, i.e.
    `source == SDG` -- excludes uploaded seeds (`source == SEED`/
    `UPLOADED`), which are synchronous and never go through the SDG
    Celery task these numbers describe.
    """
    stmt = stmt.where(
        Dataset.source == DatasetSource.SDG,
        Dataset.created_at >= start,
        Dataset.created_at < end,
    )
    stmt = ownership.scope_datasets_to_owner(stmt, user)
    if project_id is not None:
        stmt = stmt.where(Dataset.project_id == project_id)
    return stmt


def _scope_training(
    stmt: Select,
    *,
    user: CurrentUser | None,
    project_id: UUID | None,
    start: datetime,
    end: datetime,
) -> Select:
    stmt = stmt.where(TrainingJob.created_at >= start, TrainingJob.created_at < end)
    stmt = ownership.scope_trainings_to_owner(stmt, user)
    if project_id is not None:
        stmt = stmt.where(TrainingJob.project_id == project_id)
    return stmt


def _scope_evaluation(
    stmt: Select,
    *,
    user: CurrentUser | None,
    project_id: UUID | None,
    start: datetime,
    end: datetime,
) -> Select:
    """`EvaluationRun` carries no `project_id` of its own -- same two-hop
    path `ownership.assert_evaluation_access`/`scope_evaluations_to_owner`
    walk (`EvaluationRun -> ModelArtifact -> TrainingJob.project_id`), not
    `EvaluationRun.dataset_id`, which has no ownership meaning here.

    `scope_evaluations_to_owner` already performs this exact join whenever
    `user is not None`. Joining it a second time for the `project_id`
    filter would put `model_artifacts`/`training_jobs` in the FROM clause
    twice (a duplicate-table SQL error), so the manual join below only
    fires for the anonymous case, where that helper is a no-op.
    """
    stmt = stmt.where(EvaluationRun.created_at >= start, EvaluationRun.created_at < end)
    if project_id is not None and user is None:
        stmt = stmt.join(
            ModelArtifact, ModelArtifact.id == EvaluationRun.model_artifact_id
        ).join(TrainingJob, TrainingJob.id == ModelArtifact.training_job_id)
    stmt = ownership.scope_evaluations_to_owner(stmt, user)
    if project_id is not None:
        stmt = stmt.where(TrainingJob.project_id == project_id)
    return stmt


async def _day_status_counts(
    db: AsyncSession, created_at_col, status_col, scope_fn, **scope_kwargs
) -> list[tuple[date, JobStatus, int]]:
    """`GROUP BY (day, status)` row counts for one stage -- feeds both that
    stage's `total`/`by_status` (summed over days) and its per-day slice of
    `series` (kept per-day), so this is the only query that stage needs for
    counts.
    """
    day_expr = _day_bucket(created_at_col)
    stmt = scope_fn(select(day_expr, status_col, func.count()), **scope_kwargs)
    stmt = stmt.group_by(day_expr, status_col)
    rows = (await db.execute(stmt)).all()
    return [(_date_for_bucket(day), stat, int(n)) for day, stat, n in rows]


async def _avg_timing(
    db: AsyncSession, started_col, ended_col, created_col, scope_fn, **scope_kwargs
) -> tuple[float | None, float | None]:
    """Mean duration/queue-wait in seconds for one stage. NULL propagates
    through the subtraction whenever the relevant column is NULL, and
    `AVG()` silently skips NULL inputs (standard SQL) -- so this already
    means "over rows having both started_at and ended_at" (duration) /
    "over rows having started_at" (queue wait) with no extra WHERE needed.
    """
    duration_expr = extract("epoch", ended_col) - extract("epoch", started_col)
    queue_expr = extract("epoch", started_col) - extract("epoch", created_col)
    stmt = scope_fn(select(func.avg(duration_expr), func.avg(queue_expr)), **scope_kwargs)
    avg_duration, avg_queue = (await db.execute(stmt)).one()
    return (
        float(avg_duration) if avg_duration is not None else None,
        float(avg_queue) if avg_queue is not None else None,
    )


def _build_stage(
    name: str,
    rows: list[tuple[date, JobStatus, int]],
    avg_duration: float | None,
    avg_queue: float | None,
) -> AnalyticsStage:
    counts: dict[JobStatus, int] = {}
    for _day, stat, n in rows:
        counts[stat] = counts.get(stat, 0) + n
    return AnalyticsStage(
        stage=name,
        total=sum(counts.values()),
        by_status=_status_counts(counts),
        avg_duration_seconds=avg_duration,
        avg_queue_wait_seconds=avg_queue,
    )


async def get_analytics(
    db: AsyncSession,
    *,
    user: CurrentUser | None,
    date_from: date | None,
    date_to: date | None,
    project_id: UUID | None,
) -> AnalyticsResponse:
    resolved_from, resolved_to = _resolve_window(date_from, date_to)
    start = datetime.combine(resolved_from, datetime.min.time(), tzinfo=UTC)
    end = datetime.combine(resolved_to + timedelta(days=1), datetime.min.time(), tzinfo=UTC)

    # 403/404 up front, before any aggregate touches the DB -- same ADR-012
    # posture as every other project-scoped read (e.g. usage_service.
    # list_project_usage): a non-owner never gets an empty-but-200 body.
    if project_id is not None:
        await ownership.assert_project_access(db, project_id, user)

    scope_kwargs = {"user": user, "project_id": project_id, "start": start, "end": end}

    sdg_rows = await _day_status_counts(
        db, Dataset.created_at, Dataset.status, _scope_sdg, **scope_kwargs
    )
    training_rows = await _day_status_counts(
        db, TrainingJob.created_at, TrainingJob.status, _scope_training, **scope_kwargs
    )
    evaluation_rows = await _day_status_counts(
        db, EvaluationRun.created_at, EvaluationRun.status, _scope_evaluation, **scope_kwargs
    )

    # Dataset has no started_at/ended_at (see api/models/dataset.py) -- sdg
    # timings are always null, no query needed for them.
    training_avg_duration, training_avg_queue = await _avg_timing(
        db,
        TrainingJob.started_at,
        TrainingJob.ended_at,
        TrainingJob.created_at,
        _scope_training,
        **scope_kwargs,
    )
    evaluation_avg_duration, evaluation_avg_queue = await _avg_timing(
        db,
        EvaluationRun.started_at,
        EvaluationRun.ended_at,
        EvaluationRun.created_at,
        _scope_evaluation,
        **scope_kwargs,
    )

    stages = [
        _build_stage("sdg", sdg_rows, None, None),
        _build_stage("training", training_rows, training_avg_duration, training_avg_queue),
        _build_stage(
            "evaluation", evaluation_rows, evaluation_avg_duration, evaluation_avg_queue
        ),
    ]

    # ---- usage totals (prompt/completion tokens, cost, has_unpriced) -----
    # Same shape as usage_service.summary_for_actor's totals_stmt, plus the
    # window bound and optional project filter that endpoint doesn't need.
    usage_totals_stmt = select(
        func.coalesce(func.sum(UsageEvent.prompt_tokens), 0),
        func.coalesce(func.sum(UsageEvent.completion_tokens), 0),
        func.sum(UsageEvent.cost_usd),
        func.sum(case((UsageEvent.cost_usd.is_(None), 1), else_=0)),
    ).where(UsageEvent.created_at >= start, UsageEvent.created_at < end)
    usage_totals_stmt = _usage_actor_filter(usage_totals_stmt, user)
    if project_id is not None:
        usage_totals_stmt = usage_totals_stmt.where(UsageEvent.project_id == project_id)
    prompt_tokens, completion_tokens, cost_sum, unpriced_rows = (
        await db.execute(usage_totals_stmt)
    ).one()

    # ---- usage cost per day (for `series`) --------------------------------
    usage_day_expr = _day_bucket(UsageEvent.created_at)
    usage_day_stmt = select(usage_day_expr, func.sum(UsageEvent.cost_usd)).where(
        UsageEvent.created_at >= start, UsageEvent.created_at < end
    )
    usage_day_stmt = _usage_actor_filter(usage_day_stmt, user)
    if project_id is not None:
        usage_day_stmt = usage_day_stmt.where(UsageEvent.project_id == project_id)
    usage_day_stmt = usage_day_stmt.group_by(usage_day_expr)
    daily_cost = {
        _date_for_bucket(day): _to_decimal(cost)
        for day, cost in (await db.execute(usage_day_stmt)).all()
    }

    # ---- totals.by_status: element-wise sum of the three stages' maps ----
    total_counts: dict[JobStatus, int] = {s: 0 for s in JobStatus}
    for stage in stages:
        for s in JobStatus:
            total_counts[s] += getattr(stage.by_status, s.value)

    totals = AnalyticsTotals(
        jobs_total=sum(stage.total for stage in stages),
        by_status=_status_counts(total_counts),
        prompt_tokens=int(prompt_tokens or 0),
        completion_tokens=int(completion_tokens or 0),
        cost_usd=_to_decimal(cost_sum),
        has_unpriced_usage=bool(unpriced_rows),
    )

    # ---- series: one zero-filled entry per UTC day in the window ---------
    num_days = (resolved_to - resolved_from).days + 1
    series_days = [resolved_from + timedelta(days=i) for i in range(num_days)]
    series_by_day: dict[date, dict[str, int]] = {
        d: {"sdg": 0, "training": 0, "evaluation": 0, "completed": 0, "failed": 0}
        for d in series_days
    }
    for stage_key, rows in (
        ("sdg", sdg_rows),
        ("training", training_rows),
        ("evaluation", evaluation_rows),
    ):
        for day, stat, n in rows:
            bucket = series_by_day.get(day)
            if bucket is None:
                continue  # can't happen -- the query is already window-bounded
            bucket[stage_key] += n
            if stat in (JobStatus.COMPLETED, JobStatus.FAILED):
                bucket[stat.value] += n

    series = [
        AnalyticsSeriesPoint(date=d, cost_usd=daily_cost.get(d), **series_by_day[d])
        for d in series_days
    ]

    return AnalyticsResponse(
        period_from=resolved_from,
        period_to=resolved_to,
        project_id=project_id,
        totals=totals,
        stages=stages,
        series=series,
    )


__all__ = ["get_analytics"]
