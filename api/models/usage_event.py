"""UsageEvent ORM model — immutable log of OpenRouter token/cost usage.

Deliberately does NOT use `TimestampMixin`: usage rows are write-once and
never updated, so an `updated_at` column would be meaningless (and would
silently always equal `created_at`, inviting a reader to draw a false
conclusion from it). `created_at` is declared explicitly instead.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import Base, uuid_pk


class UsageEvent(Base):
    __tablename__ = "usage_events"
    __table_args__ = (
        # Backs the "usage for project X, newest first" / date-range rollup
        # query. The single-column index on `project_id` below (from
        # `index=True`) doesn't cover an ORDER BY / range scan on
        # created_at, so this composite index is separate, not redundant
        # with it.
        Index("ix_usage_events_project_id_created_at", "project_id", "created_at"),
        # Backs the per-actor monthly `SUM(cost_usd)` budget check that a
        # later task adds — that query filters by actor_id and a
        # created_at range, so it needs actor_id leading the composite.
        Index("ix_usage_events_actor_id_created_at", "actor_id", "created_at"),
    )

    id: Mapped[UUID] = uuid_pk()

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    actor_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
        doc="Supabase auth 'sub' claim of the caller who triggered this usage, if any.",
    )
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        doc=(
            "SET NULL (not CASCADE) is deliberate: billing history must "
            "outlive the project it records, so a deleted project's usage "
            "history can still be inspected."
        ),
    )
    job_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
        doc=(
            "The Celery task id — the same value the client subscribes to "
            "on /ws/jobs/{id}."
        ),
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6),
        nullable=True,
        doc=(
            "NULL means \"model not in the pricing map\" — never 0, "
            "because 0 is a lie that lets a budget cap be bypassed."
        ),
    )
    outcome: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        doc=(
            '"completed" | "failed" | "cancelled" — a plain string, not a '
            "Postgres enum, same as audit_events.outcome."
        ),
    )

    # No JSONB anywhere on this table. That's deliberate: it avoids needing
    # the `@compiles(JSONB, "sqlite")` shim that tests/unit/test_dataset_status.py
    # requires — this model's tests run against plain in-memory sqlite as-is.


__all__ = ["UsageEvent"]
