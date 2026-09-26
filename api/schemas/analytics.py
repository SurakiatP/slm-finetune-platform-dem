"""Analytics response schemas (`GET /api/v1/analytics`) -- the wire shape for
the frontend's `/analytics` page: an owner-scoped rollup of job counts/
timings and OpenRouter token/cost usage over a date window, optionally
narrowed to one project. See `api/services/analytics_service.py` for how
each field is computed and scoped.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StatusCounts(BaseModel):
    """Job counts broken down by `JobStatus`, zero-filled for every status
    value even when a bucket has no rows -- so a chart consumer never has
    to guess whether a missing key means zero or means "not computed".
    """

    model_config = ConfigDict(extra="forbid")

    pending: int = 0
    running: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0


class AnalyticsTotals(BaseModel):
    """Rollup across all three stages (sdg/training/evaluation) plus usage."""

    model_config = ConfigDict(extra="forbid")

    jobs_total: int
    by_status: StatusCounts
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal | None = Field(
        description="Null only when there are no priced rows in the window."
    )
    has_unpriced_usage: bool = Field(
        description=(
            "True when at least one UsageEvent row in this window had "
            "cost_usd IS NULL, so a consumer knows cost_usd is a floor, "
            "not a total."
        )
    )


class AnalyticsStage(BaseModel):
    """One row of the `stages` array -- always exactly `sdg`, `training`,
    `evaluation`, in that order.
    """

    model_config = ConfigDict(extra="forbid")

    stage: str
    total: int
    by_status: StatusCounts
    avg_duration_seconds: float | None = Field(
        description=(
            "Mean (ended_at - started_at) over rows having both. Always "
            "null for `sdg` -- Dataset has no started_at/ended_at columns."
        )
    )
    avg_queue_wait_seconds: float | None = Field(
        description=(
            "Mean (started_at - created_at) over rows having started_at. "
            "Always null for `sdg`, same reason as avg_duration_seconds."
        )
    )


class AnalyticsSeriesPoint(BaseModel):
    """One UTC calendar day within `[period_from, period_to]`. `series`
    always has one entry per day in the window, zero-filled, ascending --
    never sparse.
    """

    model_config = ConfigDict(extra="forbid")

    date: date
    sdg: int
    training: int
    evaluation: int
    completed: int = Field(description="Of jobs created this day, how many are COMPLETED now.")
    failed: int = Field(description="Of jobs created this day, how many are FAILED now.")
    cost_usd: Decimal | None = Field(
        description="UsageEvent cost for events created this day; null when none were priced."
    )


class AnalyticsResponse(BaseModel):
    """Response body for `GET /api/v1/analytics`."""

    model_config = ConfigDict(extra="forbid")

    period_from: date
    period_to: date
    project_id: UUID | None
    totals: AnalyticsTotals
    stages: list[AnalyticsStage]
    series: list[AnalyticsSeriesPoint]


__all__ = [
    "AnalyticsResponse",
    "AnalyticsSeriesPoint",
    "AnalyticsStage",
    "AnalyticsTotals",
    "StatusCounts",
]
