"""Application service for `/api/v1/api-keys` and key-authenticated inference.

Per-user `sk-slm-...` credentials for calling `/inference/*` without a JWT
(see `api/routers/inference.py::inference_caller`). Security invariants,
enforced throughout this module:

  * The plaintext key is generated once, at creation, and is never stored,
    logged, or returned again — only its SHA-256 hash (`ApiKey.key_hash`)
    is persisted. `create_key` is the only function that ever sees the
    plaintext; it must never appear in a log line or an exception message.
  * Lookup on `authenticate()` is by hash equality only — the plaintext
    never round-trips through a query.
  * `authenticate()`'s failure detail is generic ("invalid authentication
    token") for every failure shape (unknown hash, revoked key) — the same
    anti-oracle rule `api/core/auth.py` applies to JWTs.
  * Comparing SHA-256 hex digests via a plain DB equality lookup (not
    `hmac.compare_digest`) is fine here: the value being compared is the
    hash of a 256-bit random secret, not a secret itself, so there is no
    timing side-channel worth defending — an attacker who already knows
    the hash has already broken the one thing that matters (the plaintext
    is still unrecoverable from it).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core import request_context
from api.core.auth import CurrentUser
from api.core.config import get_settings
from api.models.api_key import ApiKey
from api.schemas.api_keys import ApiKeyCreate, ApiKeyCreatedResponse, ApiKeyResponse
from api.schemas.responses import Page
from api.services import audit_service

log = logging.getLogger("api.api_keys")

KEY_PREFIX = "sk-slm-"

# Only update `last_used_at` when it's unset or stale by more than this —
# every key-authenticated inference call would otherwise write a row on
# every single request, which is pure churn for a timestamp nobody reads
# at sub-minute granularity.
_LAST_USED_REFRESH_SECONDS = 60

_INVALID_KEY_DETAIL = "invalid authentication token"


def _actor() -> tuple[str | None, str | None]:
    return request_context.current_user_id(), request_context.current_request_id()


def generate() -> tuple[str, str]:
    """Return `(plaintext, sha256_hex)` for a fresh key. Never logged."""
    plaintext = KEY_PREFIX + secrets.token_urlsafe(32)
    digest = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    return plaintext, digest


def _status_of(key: ApiKey) -> str:
    return "revoked" if key.revoked_at is not None else "active"


def _to_response(key: ApiKey) -> ApiKeyResponse:
    return ApiKeyResponse(
        id=key.id,
        name=key.name,
        prefix=key.prefix,
        last4=key.last4,
        status=_status_of(key),  # type: ignore[arg-type]
        created_at=key.created_at,
        last_used_at=key.last_used_at,
    )


def _not_found(key_id: object) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=f"API key {key_id} not found"
    )


def _forbidden(key_id: object) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"API key {key_id} is not accessible",
    )


async def _active_count(db: AsyncSession, owner_id: str) -> int:
    stmt = (
        select(func.count())
        .select_from(ApiKey)
        .where(ApiKey.owner_id == owner_id, ApiKey.revoked_at.is_(None))
    )
    return int((await db.execute(stmt)).scalar_one())


async def create_key(
    db: AsyncSession, body: ApiKeyCreate, user: CurrentUser
) -> ApiKeyCreatedResponse:
    """Issue a new key. 409 once the caller's active-key cap is reached."""
    cap = get_settings().api_keys_max_per_user
    active = await _active_count(db, user.id)
    if active >= cap:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"API key limit reached ({active}/{cap} active). Revoke one first.",
        )

    plaintext, digest = generate()
    row = ApiKey(
        owner_id=user.id,
        name=body.name,
        key_hash=digest,
        prefix=plaintext[: len(KEY_PREFIX) + 4],
        last4=plaintext[-4:],
    )
    db.add(row)
    await db.flush()  # assigns row.id so the audit row can name it

    actor_id, request_id = _actor()
    audit_service.record(
        db,
        action="api_key.create",
        resource_type="api_key",
        resource_id=str(row.id),
        actor_id=actor_id,
        request_id=request_id,
        metadata={"name": row.name, "prefix": row.prefix},
    )
    await db.commit()
    await db.refresh(row)

    return ApiKeyCreatedResponse(**_to_response(row).model_dump(), key=plaintext)


async def list_keys(
    db: AsyncSession, user: CurrentUser, *, limit: int, offset: int
) -> Page[ApiKeyResponse]:
    """Newest first, including revoked keys — the point of this listing is
    letting an owner recognize which key is which, revoked ones included."""
    base = select(ApiKey).where(ApiKey.owner_id == user.id)
    count_stmt = select(func.count()).select_from(ApiKey).where(ApiKey.owner_id == user.id)

    total = int((await db.execute(count_stmt)).scalar_one())
    rows = (
        (
            await db.execute(
                base.order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return Page[ApiKeyResponse](
        items=[_to_response(r) for r in rows], total=total, limit=limit, offset=offset
    )


async def revoke_key(db: AsyncSession, key_id: UUID, user: CurrentUser) -> None:
    """Soft-revoke. Idempotent: revoking an already-revoked key is a no-op
    (no duplicate audit row), matching the terminal-state convention used
    for deployments (`deployments_service.stop`)."""
    row = await db.get(ApiKey, key_id)
    if row is None:
        raise _not_found(key_id)
    if row.owner_id != user.id:
        raise _forbidden(key_id)

    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        actor_id, request_id = _actor()
        audit_service.record(
            db,
            action="api_key.revoke",
            resource_type="api_key",
            resource_id=str(row.id),
            actor_id=actor_id,
            request_id=request_id,
            metadata={"name": row.name, "prefix": row.prefix},
        )
        await db.commit()


async def authenticate(db: AsyncSession, token: str) -> ApiKey:
    """Resolve a bearer token believed to be an `sk-slm-...` key.

    Hash lookup only — the plaintext `token` is used to compute the digest
    and is never itself logged or included in a query result. A revoked or
    unknown key raises the same generic 401 (`_INVALID_KEY_DETAIL`) so the
    failure carries no information about *why* it failed.
    """
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    stmt = select(ApiKey).where(ApiKey.key_hash == digest)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=_INVALID_KEY_DETAIL
        )

    request_context.set_user_id(row.owner_id)

    now = datetime.now(UTC)
    # ponytail: sqlite's DATETIME(timezone=True) round-trips as naive (no
    # offset), unlike Postgres — assume UTC rather than crash on the
    # subtraction below. A no-op against Postgres, which always returns
    # `last_used_at` timezone-aware already.
    last_used_at = row.last_used_at
    if last_used_at is not None and last_used_at.tzinfo is None:
        last_used_at = last_used_at.replace(tzinfo=UTC)
    if last_used_at is None or (now - last_used_at) > timedelta(
        seconds=_LAST_USED_REFRESH_SECONDS
    ):
        row.last_used_at = now
        await db.commit()

    return row


__all__ = [
    "KEY_PREFIX",
    "authenticate",
    "create_key",
    "generate",
    "list_keys",
    "revoke_key",
]
