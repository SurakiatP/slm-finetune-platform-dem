"""Audit event response schema (`GET /projects/{id}/audit` and friends)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AuditEventResponse(BaseModel):
    """Wire shape for an audit row.

    The ORM attribute is `event_metadata` (see `api/models/audit_event.py`
    for why `metadata` itself can't be used), but the API contract exposes
    it as `metadata` — `validation_alias` tells `model_validate(...,
    from_attributes=True)` to read the ORM's `event_metadata` attribute
    while the field itself (and therefore its JSON key) stays `metadata`.
    """

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    created_at: datetime
    actor_id: str | None
    project_id: UUID | None
    action: str
    resource_type: str
    resource_id: str | None
    outcome: str
    request_id: str | None
    metadata: dict[str, Any] | None = Field(
        default=None,
        validation_alias="event_metadata",
    )


__all__ = ["AuditEventResponse"]
