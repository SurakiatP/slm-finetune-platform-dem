"""JWT verification core — Supabase, and generic OIDC (e.g. Keycloak).

**Supabase path**: `smart-model-tune` authenticates with Supabase's new
"publishable key" format (`VITE_SUPABASE_PUBLISHABLE_KEY`), which pairs
with **asymmetric** JWTs signed by a per-project key that rotates via a
JWKS endpoint at ``{supabase_url}/auth/v1/.well-known/jwks.json``. That is
the path `verify_supabase_jwt` uses whenever `settings.supabase_url` is
set.

**OIDC path** (`settings.oidc_issuer`): the self-hosted frontend
authenticates against a generic OIDC provider instead — in practice
Keycloak. Both may be configured at once (the Supabase -> Keycloak
migration window): each token is routed by its `iss` to exactly one
provider, never tried against both. See the settings' docstrings in
`api/core/config.py` for the JWKS-URL derivation and the mandatory `oidc_audience` requirement.

A legacy **HS256 fallback** (`settings.supabase_jwt_secret`) exists only
for Supabase projects still on the old shared-secret signing key. It is
used only when both `oidc_issuer` and `supabase_url` are empty (i.e. no
JWKS path is configured) — new projects should not rely on it.

This module answers exactly one question — "who is this?" (signature,
`exp`, `aud`, `iss`, and the token's `sub`/`email` claims). The only DB
touch is `authenticate` mapping an OIDC `(iss, sub)` to its Engine actor
id via `identity_links`; it knows nothing about per-resource ownership —
"may they touch this row?" is a separate concern layered on top elsewhere.

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
from sqlalchemy.ext.asyncio import AsyncSession

from api.core import request_context
from api.core.config import Settings, get_settings
from api.core.database import get_db
from api.services.identity_links import resolve_actor

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

# Asymmetric-only algorithm allowlist, enforced on the OIDC path only. A
# self-hosted Keycloak JWKS never publishes anything else, so this is not
# about broadening what's accepted — it's about pinning what PyJWT is
# *told* to accept independently of the JWKS contents, which is what
# actually stops the classic "attacker resigns the token with the
# provider's own public key used as an HMAC secret" confusion attack.
# `none` and every HS* algorithm are never in this list.
_OIDC_ASYMMETRIC_ALGORITHMS = [
    "RS256", "RS384", "RS512",
    "ES256", "ES384", "ES512",
    "PS256", "PS384", "PS512",
]


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


@dataclass(frozen=True, slots=True)
class _Provider:
    """One trusted JWKS-backed token issuer.

    `algorithms=None` means "the single algorithm the matched JWK itself
    declares" (`signing_key.algorithm_name`) — the Supabase behaviour this
    module had before it grew OIDC support. The OIDC provider pins
    `_OIDC_ASYMMETRIC_ALGORITHMS` instead of trusting the JWKS response.
    """

    issuer: str
    audience: str
    jwks_url: str
    algorithms: list[str] | None
    # True -> `sub` is mapped to an actor id via `identity_links` (see
    # `authenticate`). False (Supabase) -> `sub` IS the actor id, which is
    # what every pre-Keycloak row's owner_id already holds.
    linked: bool


def _trusted_providers(settings: Settings) -> list[_Provider]:
    """Every JWKS-backed provider this deployment trusts, OIDC first.

    Both may be configured at once — that is the Supabase -> Keycloak
    migration window, where the old and new frontends share one API. A
    token is routed to exactly one of these by its `iss` (see
    `verify_supabase_jwt`), so the two never share a key set or a claim
    policy.

    `oidc_issuer` is kept **exactly as given** as the `iss` to match — no
    trailing-slash normalization. Keycloak's own `iss` claim never carries
    a trailing slash, so silently stripping one here would let a config
    that kept a stray slash start matching tokens it shouldn't. The JWKS
    URL, by contrast, does `rstrip('/')` because it's building a URL path,
    not comparing a claim. `oidc_jwks_url` overrides the derived Keycloak
    layout when the container reaches the provider on a URL different from
    the public-facing issuer (e.g. an internal Docker Compose hostname).
    """
    providers: list[_Provider] = []
    if settings.oidc_issuer:
        providers.append(
            _Provider(
                issuer=settings.oidc_issuer,
                audience=settings.oidc_audience,
                jwks_url=settings.oidc_jwks_url
                or f"{settings.oidc_issuer.rstrip('/')}/protocol/openid-connect/certs",
                algorithms=_OIDC_ASYMMETRIC_ALGORITHMS,
                linked=True,
            )
        )
    if settings.supabase_url:
        base = f"{settings.supabase_url.rstrip('/')}/auth/v1"
        providers.append(
            _Provider(
                issuer=base,
                audience=settings.supabase_jwt_audience,
                jwks_url=f"{base}/.well-known/jwks.json",
                algorithms=None,
                linked=False,
            )
        )
    return providers


# Process-wide JWKS clients, one per JWKS URL so two providers' `kid`s
# never mix in one cache. Created lazily and reused across requests —
# constructing a fresh `PyJWKClient` per request would also mean a fresh,
# cold cache per request, defeating the point of caching.
_jwks_clients: dict[str, PyJWKClient] = {}


def _get_jwks_client(jwks_url: str) -> PyJWKClient:
    """Return the process-wide JWKS client for `jwks_url`, creating it once.

    `PyJWKClient` caches the fetched JWK set in-process (`cache_jwk_set`)
    for `lifespan` seconds, and — per its own implementation — refetches
    once and retries when a requested `kid` isn't in the cached set
    (key rotation), so this module does not hand-roll either behaviour.
    What this function must guarantee is that the *same* client (and
    therefore the same cache) is reused across requests.
    """
    client = _jwks_clients.get(jwks_url)
    if client is None:
        client = _jwks_clients[jwks_url] = PyJWKClient(
            jwks_url,
            cache_jwk_set=True,
            lifespan=_JWKS_CACHE_TTL_SECONDS,
        )
    return client


def _reset_jwks_client_cache() -> None:
    """Drop the cached clients so changed settings take effect.

    Not used by request-handling code — production never changes its
    providers mid-process. Exists as a test seam.
    """
    _jwks_clients.clear()


def _decode_via_jwks(token: str, provider: _Provider) -> dict[str, object]:
    """Verify `token` against `provider`'s own JWKS and claim policy.

    `exp` and `sub` are required outright on every provider, not merely
    checked when present.
    """
    signing_key = _get_jwks_client(provider.jwks_url).get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=provider.algorithms or [signing_key.algorithm_name],
        audience=provider.audience,
        issuer=provider.issuer,
        options={"require": ["exp", "sub"]},
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
    """Validate `token` and return the identity it carries (`id` = raw `sub`).

    Request handling goes through `authenticate`, which additionally maps an
    OIDC `sub` to its Engine actor id; this stays as the pure, DB-free
    verifier (and test seam). Kept under its original name even though it
    verifies more than just Supabase tokens.
    """
    return _verify(token)[0]


def _verify(token: str) -> tuple[CurrentUser, _Provider | None]:
    """Validate `token`; return the identity and the provider that issued it
    (`None` on the HS256 fallback path).
    Picks exactly one verifier:

      1. any JWKS provider configured (`_trusted_providers`: OIDC via
         `oidc_issuer`, Supabase via `supabase_url`, or both during the
         Keycloak migration) -> the one whose issuer equals the token's
         `iss` exactly. Unknown `iss` -> 401. The chosen provider checks
         signature, `iss`, `aud`, `exp`, `sub` (and `nbf` when present);
         OIDC also pins `_OIDC_ASYMMETRIC_ALGORITHMS`.
      2. else `supabase_jwt_secret` set -> legacy Supabase HS256 fallback.
      3. else -> misconfigured; fails closed with 401 rather than admit.

    Raises `HTTPException(401)` with a generic detail on any failure —
    nothing about *why* it failed, and the token itself, ever reaches the
    client or a log line.
    """
    settings = get_settings()
    providers = _trusted_providers(settings)
    provider: _Provider | None = None

    try:
        if providers:
            # The unverified `iss` only *selects* among the configured
            # providers — it is never trusted as identity and never used to
            # build a URL. No match -> 401; a match that then fails
            # verification is also 401, never retried against another one.
            iss = jwt.decode(token, options={"verify_signature": False}).get("iss")
            provider = next((p for p in providers if p.issuer == iss), None)
            if provider is None:
                log.info("rejected token (untrusted issuer)")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=_INVALID_TOKEN_DETAIL,
                )
            claims = _decode_via_jwks(token, provider)
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
    return CurrentUser(id=sub, email=email if isinstance(email, str) else None), provider


async def authenticate(token: str, db: AsyncSession) -> CurrentUser:
    """Verify `token` and resolve it to the Engine actor every ownership
    check compares against. Shared by REST (`current_user_optional`) and the
    WebSocket handshake so the two can never disagree on who a caller is.
    """
    # Offloaded to a worker thread: verification is synchronous and, on the
    # JWKS path, can perform a blocking HTTPS fetch — `PyJWKClient` uses
    # `urllib.request.urlopen`, which has no async variant. That happens
    # whenever the JWKS cache is cold or a rotated `kid` forces a refetch, and
    # calling it inline would stall the whole event loop for a network
    # round-trip. Same convention `ai_engine/data_gen/openrouter_client.py`
    # documents for its sync client.
    user, provider = await asyncio.to_thread(_verify, token)
    if provider is None or not provider.linked:
        return user
    actor_id = await resolve_actor(db, provider.issuer, user.id)
    return CurrentUser(id=actor_id, email=user.email)


async def current_user_optional(
    authorization: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_db),
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
    user = await authenticate(token, db)
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


async def require_authenticated_user(
    user: Annotated[CurrentUser | None, Depends(current_user_optional)],
) -> CurrentUser:
    """Require a verified caller, regardless of `settings.auth_required`.

    Unlike `require_user` (which only enforces once the phased rollout
    flips `AUTH_REQUIRED=true`), deployments and API keys are ownership-
    scoped resources from day one — there is no anonymous owner to bucket
    them under — so these routes always 401 on a missing/invalid token,
    even in phase 1. An invalid token still 401s inside
    `current_user_optional` itself; this only adds the missing-token case.
    """
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    return user


__all__ = [
    "CurrentUser",
    "authenticate",
    "current_user_optional",
    "extract_bearer_token",
    "require_authenticated_user",
    "require_user",
    "verify_supabase_jwt",
]
