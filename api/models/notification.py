"""Notification ORM model — one row per terminal job outcome, per owner.

Written by the workers (see `workers/notifications.py`), read by the
frontend via `/api/v1/notifications`. Deduped on (owner, event, resource).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import Base, uuid_pk


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "event_type", "resource_id", name="uq_notifications_owner_event_resource"
        ),
        Index("ix_notifications_owner_created", "owner_id", "created_at", "id"),
    )

    id: Mapped[UUID] = uuid_pk()
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Python-side default too: sqlite's server default has 1s precision and a
    # different format, which would break keyset cursors in tests.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
