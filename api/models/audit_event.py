"""AuditEvent ORM model — immutable log of who did what to which resource.

Deliberately does NOT use `TimestampMixin`: audit rows are write-once and
never updated, so an `updated_at` column would be meaningless (and would
silently always equal `created_at`, inviting a reader to draw a false
conclusion from it). `created_at` is declared explicitly instead.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import Base, uuid_pk


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        # Backs the "activity for project X, newest first" query
        # (`audit_service.list_activity`). The single-column index on
        # `project_id` below (from `index=True`) doesn't cover an
        # ORDER BY created_at, so this composite index is separate, not
        # redundant with it.
        Index("ix_audit_events_project_id_created_at", "project_id", "created_at"),
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
        doc="Supabase auth 'sub' claim of the actor who performed the action, if any.",
    )
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        doc=(
            "SET NULL (not CASCADE) is deliberate: the audit trail must "
            "outlive the project it references so a deleted project's "
            "history can still be inspected."
        ),
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outcome: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        doc='"success" or "failure" — a plain string, not a Postgres enum.',
    )
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
        doc=(
            "DB column name is 'metadata'; the Python attribute is "
            "'event_metadata' because 'metadata' is reserved on "
            "DeclarativeBase (SQLAlchemy's own MetaData registry)."
        ),
    )


__all__ = ["AuditEvent"]
