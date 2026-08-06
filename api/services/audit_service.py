"""Audit event writes + reads.

`record()` is the single write path for every audited action across the
codebase — both the async API request handlers and the sync Celery
workers call it. Both `sqlalchemy.orm.Session.add()` and
`sqlalchemy.ext.asyncio.AsyncSession.add()` are synchronous methods (only
`flush`/`commit`/`execute`/etc. need `await`), which is exactly what lets
one helper serve both call sites without an async/sync split.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from api.core.auth import CurrentUser
from api.models.audit_event import AuditEvent
from api.schemas.audit import AuditEventResponse
from api.schemas.responses import Page
from api.services import ownership


def record(
    session: Session | AsyncSession,
    *,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    project_id: UUID | None = None,
    outcome: str = "success",
    actor_id: str | None = None,
    request_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    """Build an `AuditEvent` and `session.add()` it. That's all.

    Deliberately no `commit`/`flush` and no `try/except`: this call rides
    inside the caller's own transaction, and a failing insert MUST
    propagate and fail that transaction. Swallowing it here would create
    exactly the silent gap an audit trail exists to prevent — "the action
    happened but we have no record of it" is not an acceptable outcome to
    trade away for robustness.
    """
    event = AuditEvent(
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        project_id=project_id,
        outcome=outcome,
        actor_id=actor_id,
        request_id=request_id,
        event_metadata=metadata,
    )
    session.add(event)
    return event


async def list_activity(
    db: AsyncSession,
    project_id: UUID,
    *,
    limit: int,
    offset: int,
    user: CurrentUser | None,
) -> Page[AuditEventResponse]:
    """Paginated audit trail for one project, newest first.

    `assert_project_access` runs first — before touching `audit_events` at
    all — so a non-owner gets the same 404 they'd get probing the project
    directly, rather than an empty (and therefore existence-revealing)
    page.
    """
    await ownership.assert_project_access(db, project_id, user)

    base = (
        select(AuditEvent)
        .where(AuditEvent.project_id == project_id)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
    )
    count = (
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.project_id == project_id)
    )

    total = int((await db.execute(count)).scalar_one())
    rows = (await db.execute(base.limit(limit).offset(offset))).scalars().all()

    return Page[AuditEventResponse](
        items=[AuditEventResponse.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


__all__ = ["record", "list_activity"]
