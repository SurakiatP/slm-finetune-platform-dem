"""Usage event response schemas (`GET /projects/{id}/usage` and friends).

These are the wire contract a later task's read API implements — defined
fully now so that task only has to wire a router/service against an
already-agreed shape.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class UsageEventResponse(BaseModel):
    """Wire shape for a single usage row."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    created_at: datetime
    actor_id: str | None
    project_id: UUID | None
    job_id: str | None
    provider: str
    model: str
    stage: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal | None
    outcome: str


class UsageRollupItem(BaseModel):
    """One (model, stage) bucket within a `UsageSummaryResponse`."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    model: str
    stage: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal | None


class UsageSummaryResponse(BaseModel):
    """Aggregate usage/cost over a date range, broken down by model+stage."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    period_start: datetime
    period_end: datetime
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal | None
    items: list[UsageRollupItem]
    has_unpriced_usage: bool = Field(
        description=(
            "True when at least one row in this period had cost_usd IS "
            "NULL, so a consumer knows the total is a floor, not a total."
        )
    )


__all__ = ["UsageEventResponse", "UsageRollupItem", "UsageSummaryResponse"]
