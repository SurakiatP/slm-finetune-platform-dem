"""API key request / response schemas.

Per-user `sk-slm-...` keys for key-authenticated `/inference/*` calls (see
api/services/api_keys_service.py). The plaintext key is generated once at
creation time and never stored or returned again — only its SHA-256 hash is
persisted, plus a `prefix`/`last4` pair for the caller to recognize which key
is which without re-displaying it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ApiKeyCreate(BaseModel):
    """Body for `POST /api/v1/api-keys`."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=200)


class ApiKeyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    name: str
    prefix: str
    last4: str
    # Derived from `revoked_at IS NULL`, not a stored column — never
    # "active"/"revoked" past its soft-revoke: a revoked key is never
    # un-revoked.
    status: Literal["active", "revoked"]
    created_at: datetime
    last_used_at: datetime | None


class ApiKeyCreatedResponse(ApiKeyResponse):
    """201 body for a freshly created key — the only response that ever
    carries the plaintext value. Shown once; it is not retrievable again.
    """

    key: str = Field(..., description="Plaintext sk-slm-... key. Shown once.")


__all__ = ["ApiKeyCreate", "ApiKeyCreatedResponse", "ApiKeyResponse"]
