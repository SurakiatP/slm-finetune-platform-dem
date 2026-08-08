"""Unit tests for Supabase JWT verification (ADR-009).

`api/core/auth.py` answers one question — "who is this?" — and the whole
two-phase rollout rests on one subtlety it must get right:

    *absent* is tolerated in phase 1; *invalid* never is, in either phase.

If that distinction slipped, phase 1 would accept forged tokens, and the
compatibility window designed to keep the current frontend working would
instead be an open door.

The claim-validation cases run over the HS256 path (cheap, no keypair, no
network). The JWKS path — what production actually uses, since Supabase's
new publishable-key format signs asymmetrically — is covered separately with
a locally generated RSA key and a stubbed signing-key lookup, so nothing here
touches the network.
"""

from __future__ import annotations

import time

import jwt
import pytest
from fastapi import HTTPException

from api.core import auth as auth_mod
from api.core.auth import (
    CurrentUser,
    current_user_optional,
    extract_bearer_token,
    require_user,
    verify_supabase_jwt,
)
from api.core.config import get_settings

# >=32 bytes: PyJWT warns below that for HS256 (RFC 7518 §3.2).
_SECRET = "test-hs256-secret-padded-to-32-bytes-min"
_AUD = "authenticated"
_SUB = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def hs256_settings(monkeypatch: pytest.MonkeyPatch):
    """Configure the HS256 fallback path (no `SUPABASE_URL` -> no JWKS)."""
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", _SECRET)
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", _AUD)
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    get_settings.cache_clear()
    auth_mod._reset_jwks_client_cache()
    yield
    get_settings.cache_clear()
    auth_mod._reset_jwks_client_cache()


def _hs256(**overrides) -> str:
    claims = {
        "sub": _SUB,
        "aud": _AUD,
        "email": "a@example.com",
        "exp": int(time.time()) + 3600,
        **overrides,
    }
    return jwt.encode(claims, _SECRET, algorithm="HS256")


# =============================================================================
# 1. Bearer parsing
# =============================================================================


class TestExtractBearerToken:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Bearer abc", "abc"),
            ("bearer abc", "abc"),
            ("BEARER abc", "abc"),
            ("Bearer   abc  ", "abc"),
        ],
    )
    def test_accepts_valid_shapes(self, raw: str, expected: str) -> None:
        assert extract_bearer_token(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "abc", "Basic abc", "Bearer", "Bearer   "])
    def test_rejects_everything_else(self, raw) -> None:
        assert extract_bearer_token(raw) is None


# =============================================================================
# 2. Claim validation (HS256 path)
# =============================================================================


class TestVerifyClaims:
    def test_valid_token_yields_the_sub_and_email(self, hs256_settings) -> None:
        user = verify_supabase_jwt(_hs256())
        assert isinstance(user, CurrentUser)
        assert user.id == _SUB
        assert user.email == "a@example.com"

    def test_expired_token_is_rejected(self, hs256_settings) -> None:
        with pytest.raises(HTTPException) as exc:
            verify_supabase_jwt(_hs256(exp=int(time.time()) - 10))
        assert exc.value.status_code == 401

    def test_wrong_audience_is_rejected(self, hs256_settings) -> None:
        """A token minted for a different Supabase audience is not ours."""
        with pytest.raises(HTTPException) as exc:
            verify_supabase_jwt(_hs256(aud="some-other-service"))
        assert exc.value.status_code == 401

    def test_wrong_signature_is_rejected(self, hs256_settings) -> None:
        forged = jwt.encode(
            {"sub": _SUB, "aud": _AUD, "exp": int(time.time()) + 3600},
            "not-the-secret-but-also-32-bytes-long-ok",
            algorithm="HS256",
        )
        with pytest.raises(HTTPException) as exc:
            verify_supabase_jwt(forged)
        assert exc.value.status_code == 401

    def test_alg_none_is_rejected(self, hs256_settings) -> None:
        """The classic JWT bypass: an unsigned token claiming alg=none."""
        unsigned = jwt.encode(
            {"sub": _SUB, "aud": _AUD, "exp": int(time.time()) + 3600},
            key="",
            algorithm="none",
        )
        with pytest.raises(HTTPException) as exc:
            verify_supabase_jwt(unsigned)
        assert exc.value.status_code == 401

    def test_garbage_is_rejected(self, hs256_settings) -> None:
        with pytest.raises(HTTPException) as exc:
            verify_supabase_jwt("not-a-jwt-at-all")
        assert exc.value.status_code == 401

    def test_missing_sub_is_rejected(self, hs256_settings) -> None:
        """A signature-valid token with no subject identifies nobody."""
        token = jwt.encode(
            {"aud": _AUD, "exp": int(time.time()) + 3600}, _SECRET, algorithm="HS256"
        )
        with pytest.raises(HTTPException) as exc:
            verify_supabase_jwt(token)
        assert exc.value.status_code == 401

    def test_missing_email_is_allowed(self, hs256_settings) -> None:
        """Identity is the `sub`; email is decoration (OAuth users may lack one)."""
        token = jwt.encode(
            {"sub": _SUB, "aud": _AUD, "exp": int(time.time()) + 3600},
            _SECRET,
            algorithm="HS256",
        )
        assert verify_supabase_jwt(token).email is None

    def test_failure_detail_never_leaks_the_reason(self, hs256_settings) -> None:
        """Expired and forged must be indistinguishable to the caller."""
        details = set()
        for tok in (_hs256(exp=int(time.time()) - 10), "not-a-jwt-at-all"):
            with pytest.raises(HTTPException) as exc:
                verify_supabase_jwt(tok)
            details.add(exc.value.detail)
        assert len(details) == 1, f"detail differs per failure mode: {details}"

    def test_misconfigured_backend_rejects_rather_than_admits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No JWKS URL and no secret must fail closed, not wave tokens through."""
        monkeypatch.setenv("SUPABASE_URL", "")
        monkeypatch.setenv("SUPABASE_JWT_SECRET", "")
        get_settings.cache_clear()
        auth_mod._reset_jwks_client_cache()
        try:
            with pytest.raises(HTTPException) as exc:
                verify_supabase_jwt(_hs256())
            assert exc.value.status_code == 401
        finally:
            get_settings.cache_clear()
            auth_mod._reset_jwks_client_cache()


# =============================================================================
# 3. The optional-vs-invalid rule — the heart of the two-phase rollout
# =============================================================================


class TestOptionalVsInvalid:
    async def test_absent_header_is_anonymous(self, hs256_settings) -> None:
        assert await current_user_optional(None) is None

    async def test_malformed_header_is_treated_as_absent(self, hs256_settings) -> None:
        """A non-Bearer scheme can come from proxies we don't control."""
        assert await current_user_optional("Basic abc") is None

    async def test_valid_header_resolves_the_user(self, hs256_settings) -> None:
        user = await current_user_optional(f"Bearer {_hs256()}")
        assert user is not None and user.id == _SUB

    async def test_invalid_token_401s_even_in_phase_one(
        self, hs256_settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """THE rule. `AUTH_REQUIRED=false` tolerates *absence*, never garbage —
        otherwise phase 1 would accept forged tokens."""
        monkeypatch.setenv("AUTH_REQUIRED", "false")
        get_settings.cache_clear()
        assert get_settings().auth_required is False

        with pytest.raises(HTTPException) as exc:
            await current_user_optional("Bearer definitely-not-valid")
        assert exc.value.status_code == 401


class TestRequireUser:
    async def test_phase_one_allows_anonymous(self, hs256_settings) -> None:
        assert get_settings().auth_required is False
        assert await require_user(None) is None

    async def test_phase_two_rejects_anonymous(
        self, hs256_settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AUTH_REQUIRED", "true")
        get_settings.cache_clear()
        with pytest.raises(HTTPException) as exc:
            await require_user(None)
        assert exc.value.status_code == 401

    async def test_phase_two_passes_an_authenticated_user_through(
        self, hs256_settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AUTH_REQUIRED", "true")
        get_settings.cache_clear()
        user = CurrentUser(id=_SUB, email=None)
        assert await require_user(user) is user


# =============================================================================
# 4. JWKS path (production) — local RSA key, no network
# =============================================================================


@pytest.fixture
def jwks_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "")
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", _AUD)
    get_settings.cache_clear()
    auth_mod._reset_jwks_client_cache()
    yield
    get_settings.cache_clear()
    auth_mod._reset_jwks_client_cache()


@pytest.fixture
def rsa_keypair():
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key, key.public_key()


@pytest.fixture
def stub_jwks(monkeypatch: pytest.MonkeyPatch, rsa_keypair):
    """Point the signing-key lookup at our local public key — no HTTP."""
    _, public_key = rsa_keypair

    class _Key:
        key = public_key
        algorithm_name = "RS256"

    class _Client:
        @staticmethod
        def get_signing_key_from_jwt(_token: str):
            return _Key()

    monkeypatch.setattr(auth_mod, "_get_jwks_client", lambda: _Client())


class TestJwksPath:
    def test_valid_rs256_token_is_accepted(
        self, jwks_settings, stub_jwks, rsa_keypair
    ) -> None:
        private_key, _ = rsa_keypair
        token = jwt.encode(
            {
                "sub": _SUB,
                "aud": _AUD,
                "iss": "https://proj.supabase.co/auth/v1",
                "exp": int(time.time()) + 3600,
            },
            private_key,
            algorithm="RS256",
        )
        assert verify_supabase_jwt(token).id == _SUB

    def test_wrong_issuer_is_rejected(self, jwks_settings, stub_jwks, rsa_keypair) -> None:
        """A correctly-signed token from a *different* Supabase project."""
        private_key, _ = rsa_keypair
        token = jwt.encode(
            {
                "sub": _SUB,
                "aud": _AUD,
                "iss": "https://someone-else.supabase.co/auth/v1",
                "exp": int(time.time()) + 3600,
            },
            private_key,
            algorithm="RS256",
        )
        with pytest.raises(HTTPException) as exc:
            verify_supabase_jwt(token)
        assert exc.value.status_code == 401

    def test_jwks_client_is_built_once_and_reused(self, jwks_settings) -> None:
        """A per-request client would mean a per-request cold cache."""
        first = auth_mod._get_jwks_client()
        assert auth_mod._get_jwks_client() is first

    def test_jwks_cache_ttl_is_pinned_not_inherited(self) -> None:
        """PyJWT's own default is 300s; ADR-009 pins ours explicitly."""
        assert auth_mod._JWKS_CACHE_TTL_SECONDS == 600

    def test_jwks_url_matches_supabase_layout(self, jwks_settings) -> None:
        assert (
            auth_mod._get_jwks_client().uri
            == "https://proj.supabase.co/auth/v1/.well-known/jwks.json"
        )
