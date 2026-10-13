"""Notification response schemas for `/api/v1/notifications`."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_type: Literal[
        "dataset.completed",
        "dataset.failed",
        "training.completed",
        "training.failed",
        "evaluation.completed",
        "evaluation.failed",
        "export.completed",
        "export.failed",
        "deployment.ready",
        "deployment.failed",
    ]
    resource_type: Literal["dataset", "training", "evaluation", "model", "deployment"]
    resource_id: str
    payload: dict[str, str | int | float | bool | None]
    created_at: datetime
    read_at: datetime | None


class NotificationPage(BaseModel):
    items: list[NotificationResponse]
    next_cursor: str | None
    unread_count: int


class MarkAllReadResponse(BaseModel):
    marked_count: int


__all__ = ["MarkAllReadResponse", "NotificationPage", "NotificationResponse"]
