"""Application service for `/api/v1/notifications` (per-owner inbox).

Keyset pagination on `(created_at, id)` descending; the cursor is an opaque
urlsafe-base64 of `"<created_at iso>|<id>"`. Rows of other owners are never
visible: a foreign id is a 404, indistinguishable from a missing one.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.auth import CurrentUser
from api.models.notification import Notification
from api.schemas.notifications import (
    MarkAllReadResponse,
    NotificationPage,
    NotificationResponse,
)


def _encode(row: Notification) -> str:
    raw = f"{row.created_at.isoformat()}|{row.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts, _, ident = raw.partition("|")
        return datetime.fromisoformat(ts), UUID(ident)
    except ValueError as exc:  # binascii.Error and UnicodeDecodeError are ValueErrors
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid cursor"
        ) from exc


async def list_notifications(
    db: AsyncSession, user: CurrentUser, *, limit: int, cursor: str | None
) -> NotificationPage:
    stmt = select(Notification).where(Notification.owner_id == user.id)
    if cursor:
        created_at, ident = _decode(cursor)
        stmt = stmt.where(
            tuple_(Notification.created_at, Notification.id) < tuple_(created_at, ident)
        )
    stmt = stmt.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]

    unread = (
        await db.execute(
            select(func.count())
            .select_from(Notification)
            .where(Notification.owner_id == user.id, Notification.read_at.is_(None))
        )
    ).scalar_one()
    return NotificationPage(
        items=[NotificationResponse.model_validate(r) for r in rows],
        next_cursor=_encode(rows[-1]) if has_more else None,
        unread_count=int(unread),
    )


async def mark_read(
    db: AsyncSession, notification_id: UUID, user: CurrentUser
) -> NotificationResponse:
    """Idempotent: an already-read row keeps its original `read_at`."""
    row = await db.get(Notification, notification_id)
    if row is None or row.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Notification {notification_id} not found",
        )
    if row.read_at is None:
        row.read_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(row)
    return NotificationResponse.model_validate(row)


async def mark_all_read(db: AsyncSession, user: CurrentUser) -> MarkAllReadResponse:
    result = await db.execute(
        update(Notification)
        .where(Notification.owner_id == user.id, Notification.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    )
    await db.commit()
    return MarkAllReadResponse(marked_count=result.rowcount or 0)


__all__ = ["list_notifications", "mark_all_read", "mark_read"]
