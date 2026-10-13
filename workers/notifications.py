"""Sync helper that records one notification per terminal job outcome.

One row per owner per (event, resource), deduped by a unique constraint. The
frontend polls `/api/v1/notifications`. Cancelled jobs are never recorded —
callers only call this on COMPLETED / FAILED.

Recording is best-effort: a failure here must never fail the job itself.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from api.models.notification import Notification

log = logging.getLogger(__name__)

__all__ = ["notification_insert", "record_notification"]

_MESSAGE_MAX = 300


def notification_insert(
    dialect_name: str,
    *,
    owner_id: str,
    event_type: str,
    resource_type: str,
    resource_id: Any,
    payload: dict[str, Any],
):
    """INSERT ... ON CONFLICT DO NOTHING for the given dialect (default postgres)."""
    clean: dict[str, Any] = {k: str(v) if isinstance(v, UUID) else v for k, v in payload.items()}
    if isinstance(clean.get("message"), str):
        clean["message"] = clean["message"][:_MESSAGE_MAX]
    insert = sqlite.insert if dialect_name == "sqlite" else postgresql.insert
    return (
        insert(Notification)
        .values(
            owner_id=owner_id,
            event_type=event_type,
            resource_type=resource_type,
            resource_id=str(resource_id),
            payload=clean,
        )
        .on_conflict_do_nothing(index_elements=["owner_id", "event_type", "resource_id"])
    )


def record_notification(
    session: Session,
    *,
    owner_id: str | None,
    event_type: str,
    resource_type: str,
    resource_id: Any,
    payload: dict[str, Any],
) -> None:
    """Insert a notification in a SAVEPOINT; log and swallow any failure."""
    if not owner_id:
        return
    # Outside the try on purpose: the job's own pending writes must surface
    # their errors unchanged (and this avoids pysqlite's SAVEPOINT-first quirk).
    session.flush()
    try:
        with session.begin_nested():
            session.execute(
                notification_insert(
                    session.get_bind().dialect.name,
                    owner_id=owner_id,
                    event_type=event_type,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    payload=payload,
                )
            )
    except Exception:
        log.warning("failed to record notification %s for %s", event_type, resource_id, exc_info=True)
