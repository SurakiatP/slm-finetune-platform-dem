"""Notifications router — the caller's inbox of terminal job outcomes.

Always requires a verified caller (`require_authenticated_user`), regardless
of `settings.auth_required`. Mounted in `api/main.py` without `_AUTH`.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.auth import CurrentUser, require_authenticated_user
from api.core.database import get_db
from api.schemas.notifications import (
    MarkAllReadResponse,
    NotificationPage,
    NotificationResponse,
)
from api.services import notifications_service

router = APIRouter()


@router.get("", response_model=NotificationPage, summary="List your notifications (newest first)")
async def list_notifications(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_authenticated_user)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: str | None = None,
) -> NotificationPage:
    return await notifications_service.list_notifications(db, user, limit=limit, cursor=cursor)


@router.post(
    "/read-all", response_model=MarkAllReadResponse, summary="Mark all your notifications read"
)
async def read_all(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_authenticated_user)],
) -> MarkAllReadResponse:
    return await notifications_service.mark_all_read(db, user)


@router.patch(
    "/{notification_id}/read",
    response_model=NotificationResponse,
    summary="Mark one notification read (idempotent)",
)
async def read_one(
    notification_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_authenticated_user)],
) -> NotificationResponse:
    return await notifications_service.mark_read(db, notification_id, user)
