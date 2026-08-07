"""Supabase JWT verification core.

**Production path**: `smart-model-tune` authenticates with Supabase's new
"publishable key" format (`VITE_SUPABASE_PUBLISHABLE_KEY`), which pairs
with **asymmetric** JWTs signed by a per-project key that rotates via a
JWKS endpoint at ``{supabase_url}/auth/v1/.well-known/jwks.json``. That is
the path `verify_supabase_jwt` uses whenever `settings.supabase_url` is
set, and is what production is expected to run.

A legacy **HS256 fallback** (`settings.supabase_jwt_secret`) exists only
for Supabase projects still on the old shared-secret signing key. It is
used only when `supabase_url` is empty (i.e. JWKS is not configured) —
new projects should not rely on it.

This module answers exactly one question — "who is this?" (signature,
`exp`, `aud`, `iss`, and the token's `sub`/`email` claims). It does not
import anything DB-related and knows nothing about per-resource
ownership; "may they touch this row?" is a separate concern layered on
top elsewhere.

**Two-phase rollout** (`settings.auth_required`, defaults to `False`):
  - Phase 1 — compatibility mode: a token is verified when present, but a
    request with *no* `Authorization` header is still let through as
    anonymous, because the current `smart-model-tune` frontend does not
    send the header yet. `current_user_optional` implements this half.
  - Phase 2 — enforced: once the frontend is confirmed to send the
    header, flip `AUTH_REQUIRED=true`; `require_user` then also rejects a
    *missing* token with 401.

In **both** phases, a token that IS present but fails verification is
always a 401 — "optional" tolerates absence, not garbage.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, status
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError, PyJWKClientError

from api.core import request_context
from api.core.config import Settings, get_settings

log = logging.getLogger("api.auth")

# TTL for the in-process JWKS cache (PyJWKClient's `lifespan`). Set
# explicitly rather than relying on PyJWT's own default, per the rollout
# plan's instruction to confirm and pin the caching behaviour rather than
# assume it. Independent of the "unknown kid -> refetch once" path below,
# which always bypasses this TTL to pick up key rotation immediately.
_JWKS_CACHE_TTL_SECONDS = 600

# Generic, non-leaky detail returned for every verification failure. The
# specific reason (expired / bad signature / wrong audience / ...) is
# logged server-side only, and the raw token is never included in either.
_INVALID_TOKEN_DETAIL = "invalid authentication token"


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """The authenticated caller, derived from a verified Supabase JWT."""

    id: str
    email: str | None


def extract_bearer_token(value: str | None) -> str | None:
    """Pull the token out of a raw ``"Bearer <token>"`` string.

    Shared by the `Authorization` header dependency below and by the
    WebSocket endpoint, which receives the same shape of credential over
    the `Sec-WebSocket-Protocol` subprotocol instead of a header and must
    not duplicate this parsing. Returns `None` if `value` is missing, has
    the wrong scheme, or carries no token.
    """
    if not value:
        return None
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


# Process-wide JWKS client. Created lazily (settings must be loaded first)
# and reused across requests — constructing a fresh `PyJWKClient` per
# request would also mean a fresh, cold cache per request, defeating the
# point of caching.
_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    """Return the process-wide JWKS client, creating it on first use.

    `PyJWKClient` caches the fetched JWK set in-process (`cache_jwk_set`)
    for `lifespan` seconds, and — per its own implementation — refetches
    once and retries when a requested `kid` isn't in the cached set
    (key rotation), so this module does not hand-roll either behaviour.
    What this function must guarantee is that the *same* client (and
    therefore the same cache) is reused across requests.
    """
    global _jwks_client
    if _jwks_client is None:
        settings = get_settings()
        jwks_url = f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
        _jwks_client = PyJWKClient(
            jwks_url,
            cache_jwk_set=True,
            lifespan=_JWKS_CACHE_TTL_SECONDS,
        )
    return _jwks_client


def _reset_jwks_client_cache() -> None:
    """Drop the cached client so a changed `supabase_url` takes effect.

    Not used by request-handling code — production never changes
    `supabase_url` mid-process. Exists as a test seam.
    """
    global _jwks_client
    _jwks_client = None


def _expected_issuer(settings: Settings) -> str | None:
    if not settings.supabase_url:
        return None
    return f"{settings.supabase_url.rstrip('/')}/auth/v1"


def _decode_via_jwks(token: str, settings: Settings, issuer: str) -> dict[str, object]:
    client = _get_jwks_client()
    signing_key = client.get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=[signing_key.algorithm_name],
        audience=settings.supabase_jwt_audience,
        issuer=issuer,
    )


def _decode_via_hs256(token: str, settings: Settings) -> dict[str, object]:
    # This branch only runs when `supabase_url` is empty (see
    # verify_supabase_jwt), so there is no known `iss` to check against —
    # `issuer` is left unset, which makes PyJWT skip that check entirely.
    return jwt.decode(
        token,
        settings.supabase_jwt_secret,
        algorithms=["HS256"],
        audience=settings.supabase_jwt_audience,
    )


def verify_supabase_jwt(token: str) -> CurrentUser:
    """Validate `token` and return the caller it identifies.

    Verifies signature, `exp`, `aud` (against `supabase_jwt_audience`) and,
    on the JWKS path, `iss` (must equal `{supabase_url}/auth/v1`). Raises
    `HTTPException(401)` with a generic detail on any failure — nothing
    about *why* it failed, and the token itself, ever reaches the client
    or a log line.
    """
    settings = get_settings()
    issuer = _expected_issuer(settings)

    try:
        if settings.supabase_url:
            claims = _decode_via_jwks(token, settings, issuer)  # type: ignore[arg-type]
        elif settings.supabase_jwt_secret:
            claims = _decode_via_hs256(token, settings)
        else:
            log.error(
                "auth misconfigured: neither supabase_url nor supabase_jwt_secret is set"
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=_INVALID_TOKEN_DETAIL,
            )
    except HTTPException:
        raise
    except PyJWKClientError as exc:
        log.warning("jwks signing-key lookup failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_INVALID_TOKEN_DETAIL,
        ) from None
    except InvalidTokenError as exc:
        log.info("rejected token (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_INVALID_TOKEN_DETAIL,
        ) from None

    sub = claims.get("sub")
    if not sub or not isinstance(sub, str):
        log.warning("token verified but 'sub' claim is missing or non-string")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_INVALID_TOKEN_DETAIL,
        )
    email = claims.get("email")
    return CurrentUser(id=sub, email=email if isinstance(email, str) else None)


async def current_user_optional(
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentUser | None:
    """Resolve the caller from `Authorization: Bearer <token>`, if present.

    Absence is tolerated — a missing header returns `None`, which is what
    lets phase-1 (`auth_required=False`) accept unauthenticated requests.
    A header that IS present but doesn't verify is **never** tolerated: it
    always raises 401, in both rollout phases. "Optional" describes the
    missing-header case only.
    """
    token = extract_bearer_token(authorization)
    if token is None:
        return None
    # Offloaded to a worker thread: `verify_supabase_jwt` is synchronous and,
    # on the JWKS path, can perform a blocking HTTPS fetch — `PyJWKClient`
    # uses `urllib.request.urlopen`, which has no async variant. That happens
    # whenever the JWKS cache is cold or a rotated `kid` forces a refetch, and
    # calling it inline would stall the whole event loop for a network
    # round-trip. Same convention `ai_engine/data_gen/openrouter_client.py`
    # documents for its sync client.
    user = await asyncio.to_thread(verify_supabase_jwt, token)
    # Bind the identified caller onto the request-scoped context so every
    # subsequent log line (including ones emitted by the request-context
    # middleware and downstream services) carries `user_id`. Anonymous
    # callers (token is None, above) leave this unset — never stamped as
    # null — matching how `request_context.bound`/`snapshot` already treat
    # unset keys elsewhere.
    request_context.set_user_id(user.id)
    return user


async def require_user(
    user: Annotated[CurrentUser | None, Depends(current_user_optional)],
) -> CurrentUser | None:
    """Enforce authentication when `auth_required=True`.

    Phase 1 (`auth_required=False`, default): behaves exactly like
    `current_user_optional` — a missing token still returns `None`
    (an invalid one already raised 401 inside `current_user_optional`).
    Phase 2 (`auth_required=True`): a missing token now also raises 401.
    """
    settings = get_settings()
    if settings.auth_required and user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    return user


__all__ = [
    "CurrentUser",
    "current_user_optional",
    "extract_bearer_token",
    "require_user",
    "verify_supabase_jwt",
]
