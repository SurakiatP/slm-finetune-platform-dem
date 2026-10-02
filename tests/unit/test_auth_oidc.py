"""Unit tests for the generic OIDC verification path (self-hosted / Keycloak).

Companion to `test_auth_token.py`, which covers the pre-existing Supabase
paths (JWKS + HS256) and the phase-1/phase-2 rollout rules — those are
untouched by this feature and are not re-tested here. This file covers only
what's new: `settings.oidc_issuer` as a third, generic OIDC verifier that
takes priority over `supabase_url`, using the same "local RSA key + stubbed
JWKS client" technique `test_auth_token.py`'s `TestJwksPath` already uses so
nothing here touches the network.
"""

from __future__ import annotations

import time

import jwt
import pytest
from fastapi import HTTPException

from api.core import auth as auth_mod
from api.core.auth import verify_supabase_jwt
from api.core.config import Settings, get_settings

_AUD = "slm-platform-frontend"
_SUB = "22222222-2222-2222-2222-222222222222"
_ISSUER = "https://keycloak.example.com/auth/realms/slm-platform"


@pytest.fixture
def rsa_keypair():
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key, key.public_key()


@pytest.fixture
def oidc_settings(monkeypatch: pytest.MonkeyPatch):
    """Configure the OIDC path. `SUPABASE_URL` is set too, on purpose: both
    providers are trusted at once (the Keycloak migration window)."""
    monkeypatch.setenv("OIDC_ISSUER", _ISSUER)
    monkeypatch.setenv("OIDC_AUDIENCE", _AUD)
    monkeypatch.setenv("OIDC_JWKS_URL", "")
    monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "")
    get_settings.cache_clear()
    auth_mod._reset_jwks_client_cache()
    yield
    get_settings.cache_clear()
    auth_mod._reset_jwks_client_cache()


@pytest.fixture
def stub_oidc_jwks(monkeypatch: pytest.MonkeyPatch, rsa_keypair):
    """Point the signing-key lookup at our local public key — no HTTP."""
    _, public_key = rsa_keypair

    class _Key:
        key = public_key
        # PyJWKClient would report the JWK's own alg; deliberately RS256
        # here since that's what these tests sign with — the allowlist
        # pinning is exercised via the token's own header alg instead
        # (test_hs256_token_on_oidc_path_is_rejected below), not via this.
        algorithm_name = "RS256"

    class _Client:
        @staticmethod
        def get_signing_key_from_jwt(_token: str):
            return _Key()

    monkeypatch.setattr(auth_mod, "_get_jwks_client", lambda _url: _Client())


def _rs256(private_key, **overrides) -> str:
    claims = {
        "sub": _SUB,
        "aud": _AUD,
        "iss": _ISSUER,
        "email": "person@example.com",
        "exp": int(time.time()) + 3600,
        **overrides,
    }
    return jwt.encode(claims, private_key, algorithm="RS256")


# =============================================================================
# 1. Claim validation
# =============================================================================


class TestOidcClaims:
    def test_valid_token_is_accepted_and_maps_sub_and_email(
        self, oidc_settings, stub_oidc_jwks, rsa_keypair
    ) -> None:
        private_key, _ = rsa_keypair
        user = verify_supabase_jwt(_rs256(private_key))
        assert user.id == _SUB
        assert user.email == "person@example.com"

    def test_wrong_issuer_is_rejected(
        self, oidc_settings, stub_oidc_jwks, rsa_keypair
    ) -> None:
        private_key, _ = rsa_keypair
        token = _rs256(private_key, iss="https://someone-else.example.com/auth/realms/x")
        with pytest.raises(HTTPException) as exc:
            verify_supabase_jwt(token)
        assert exc.value.status_code == 401

    def test_wrong_audience_is_rejected(
        self, oidc_settings, stub_oidc_jwks, rsa_keypair
    ) -> None:
        private_key, _ = rsa_keypair
        token = _rs256(private_key, aud="account")
        with pytest.raises(HTTPException) as exc:
            verify_supabase_jwt(token)
        assert exc.value.status_code == 401

    def test_expired_token_is_rejected(
        self, oidc_settings, stub_oidc_jwks, rsa_keypair
    ) -> None:
        private_key, _ = rsa_keypair
        token = _rs256(private_key, exp=int(time.time()) - 10)
        with pytest.raises(HTTPException) as exc:
            verify_supabase_jwt(token)
        assert exc.value.status_code == 401

    def test_missing_sub_is_rejected(
        self, oidc_settings, stub_oidc_jwks, rsa_keypair
    ) -> None:
        """`options={"require": ["exp", "sub"]}` on the OIDC path — sub must
        be present outright, not merely checked when it happens to be."""
        private_key, _ = rsa_keypair
        claims = {"aud": _AUD, "iss": _ISSUER, "exp": int(time.time()) + 3600}
        token = jwt.encode(claims, private_key, algorithm="RS256")
        with pytest.raises(HTTPException) as exc:
            verify_supabase_jwt(token)
        assert exc.value.status_code == 401

    def test_missing_exp_is_rejected(
        self, oidc_settings, stub_oidc_jwks, rsa_keypair
    ) -> None:
        private_key, _ = rsa_keypair
        claims = {"sub": _SUB, "aud": _AUD, "iss": _ISSUER}
        token = jwt.encode(claims, private_key, algorithm="RS256")
        with pytest.raises(HTTPException) as exc:
            verify_supabase_jwt(token)
        assert exc.value.status_code == 401

    def test_hs256_token_on_oidc_path_is_rejected(self, oidc_settings) -> None:
        """The allowlist pin: the OIDC path never accepts a symmetric-alg
        token, regardless of what the (real, unstubbed here) JWKS client
        would have returned — `jwt.decode`'s `algorithms=` list itself
        refuses to even attempt HS256 verification against an RSA key."""
        token = jwt.encode(
            {"sub": _SUB, "aud": _AUD, "iss": _ISSUER, "exp": int(time.time()) + 3600},
            "some-hs256-secret-at-least-32-bytes-long",
            algorithm="HS256",
        )
        with pytest.raises(HTTPException) as exc:
            verify_supabase_jwt(token)
        assert exc.value.status_code == 401


# =============================================================================
# 2. Precedence and JWKS URL resolution
# =============================================================================


class TestDualProviderAndJwksUrl:
    @pytest.fixture
    def two_keys(self, monkeypatch: pytest.MonkeyPatch):
        """A distinct RSA key per JWKS URL, so a token signed for one
        provider can only verify against that provider's key set."""
        from cryptography.hazmat.primitives.asymmetric import rsa

        keys = {
            f"{_ISSUER}/protocol/openid-connect/certs": rsa.generate_private_key(
                public_exponent=65537, key_size=2048
            ),
            "https://proj.supabase.co/auth/v1/.well-known/jwks.json": rsa.generate_private_key(
                public_exponent=65537, key_size=2048
            ),
        }

        class _Client:
            def __init__(self, url: str) -> None:
                self.url = url

            def get_signing_key_from_jwt(self, _token: str):
                class _Key:
                    key = keys[self.url].public_key()
                    algorithm_name = "RS256"

                return _Key()

        monkeypatch.setattr(auth_mod, "_get_jwks_client", _Client)
        return (
            keys[f"{_ISSUER}/protocol/openid-connect/certs"],
            keys["https://proj.supabase.co/auth/v1/.well-known/jwks.json"],
        )

    def test_both_issuers_are_accepted_at_once(self, oidc_settings, two_keys) -> None:
        keycloak_key, supabase_key = two_keys
        assert verify_supabase_jwt(_rs256(keycloak_key)).id == _SUB
        supabase_token = _rs256(
            supabase_key, iss="https://proj.supabase.co/auth/v1", aud="authenticated"
        )
        assert verify_supabase_jwt(supabase_token).id == _SUB

    def test_unknown_issuer_is_rejected(self, oidc_settings, two_keys) -> None:
        keycloak_key, _ = two_keys
        token = _rs256(keycloak_key, iss="https://evil.example.com/auth/realms/x")
        with pytest.raises(HTTPException) as exc:
            verify_supabase_jwt(token)
        assert exc.value.status_code == 401

    def test_token_signed_by_the_other_provider_is_rejected(
        self, oidc_settings, two_keys
    ) -> None:
        """Keycloak `iss`, Supabase key: routed to Keycloak's key set only,
        fails there, and is never retried against Supabase's."""
        _, supabase_key = two_keys
        with pytest.raises(HTTPException) as exc:
            verify_supabase_jwt(_rs256(supabase_key))
        assert exc.value.status_code == 401

    def test_each_provider_keeps_its_own_audience(self, oidc_settings, two_keys) -> None:
        """A Supabase-`iss` token carrying the Keycloak audience fails: the
        Supabase provider only accepts `authenticated`."""
        _, supabase_key = two_keys
        token = _rs256(supabase_key, iss="https://proj.supabase.co/auth/v1")
        with pytest.raises(HTTPException) as exc:
            verify_supabase_jwt(token)
        assert exc.value.status_code == 401

    def test_not_before_in_the_future_is_rejected(self, oidc_settings, two_keys) -> None:
        keycloak_key, _ = two_keys
        token = _rs256(keycloak_key, nbf=int(time.time()) + 3600)
        with pytest.raises(HTTPException) as exc:
            verify_supabase_jwt(token)
        assert exc.value.status_code == 401

    def test_each_jwks_url_gets_its_own_cached_client(self) -> None:
        auth_mod._reset_jwks_client_cache()
        try:
            a = auth_mod._get_jwks_client("https://a.example/certs")
            b = auth_mod._get_jwks_client("https://b.example/certs")
            assert a is not b
            assert auth_mod._get_jwks_client("https://a.example/certs") is a
        finally:
            auth_mod._reset_jwks_client_cache()

    def _oidc(self, settings: Settings) -> auth_mod._Provider:
        return auth_mod._trusted_providers(settings)[0]

    def test_derived_jwks_url_is_keycloak_certs_endpoint(self) -> None:
        settings = Settings(
            database_url="postgresql+asyncpg://u:p@h/d",
            oidc_issuer=_ISSUER,
            oidc_audience=_AUD,
        )
        assert self._oidc(settings).jwks_url == f"{_ISSUER}/protocol/openid-connect/certs"

    def test_explicit_jwks_url_overrides_the_derived_one(self) -> None:
        settings = Settings(
            database_url="postgresql+asyncpg://u:p@h/d",
            oidc_issuer=_ISSUER,
            oidc_audience=_AUD,
            oidc_jwks_url="http://keycloak:8080/realms/slm-platform/protocol/openid-connect/certs",
        )
        assert (
            self._oidc(settings).jwks_url
            == "http://keycloak:8080/realms/slm-platform/protocol/openid-connect/certs"
        )

    def test_trailing_slash_on_issuer_is_stripped_for_the_jwks_url_only(self) -> None:
        settings = Settings(
            database_url="postgresql+asyncpg://u:p@h/d",
            oidc_issuer=_ISSUER + "/",
            oidc_audience=_AUD,
        )
        provider = self._oidc(settings)
        assert provider.jwks_url == f"{_ISSUER}/protocol/openid-connect/certs"
        # ... but the `iss` claim comparison is exact, trailing slash and all.
        assert provider.issuer == _ISSUER + "/"


# =============================================================================
# 3. Supabase path is unaffected when OIDC settings are empty
# =============================================================================


class TestSupabaseUnchangedWhenOidcEmpty:
    def test_supabase_jwks_path_still_works_with_oidc_issuer_empty(
        self, monkeypatch: pytest.MonkeyPatch, rsa_keypair
    ) -> None:
        monkeypatch.setenv("OIDC_ISSUER", "")
        monkeypatch.setenv("OIDC_AUDIENCE", "")
        monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")
        monkeypatch.setenv("SUPABASE_JWT_SECRET", "")
        monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", "authenticated")
        get_settings.cache_clear()
        auth_mod._reset_jwks_client_cache()
        try:
            private_key, public_key = rsa_keypair

            class _Key:
                key = public_key
                algorithm_name = "RS256"

            class _Client:
                @staticmethod
                def get_signing_key_from_jwt(_token: str):
                    return _Key()

            monkeypatch.setattr(auth_mod, "_get_jwks_client", lambda _url: _Client())

            token = jwt.encode(
                {
                    "sub": _SUB,
                    "aud": "authenticated",
                    "iss": "https://proj.supabase.co/auth/v1",
                    "exp": int(time.time()) + 3600,
                },
                private_key,
                algorithm="RS256",
            )
            assert verify_supabase_jwt(token).id == _SUB
        finally:
            get_settings.cache_clear()
            auth_mod._reset_jwks_client_cache()


# =============================================================================
# 4. Config validation
# =============================================================================


class TestOidcConfigValidation:
    def test_issuer_without_audience_is_fatal_in_any_environment(self) -> None:
        with pytest.raises(Exception, match="OIDC_AUDIENCE"):
            Settings(
                database_url="postgresql+asyncpg://u:p@h/d",
                environment="dev",
                oidc_issuer=_ISSUER,
                oidc_audience="",
            )

    def test_issuer_with_audience_boots_fine_in_dev(self) -> None:
        settings = Settings(
            database_url="postgresql+asyncpg://u:p@h/d",
            environment="dev",
            oidc_issuer=_ISSUER,
            oidc_audience=_AUD,
        )
        assert settings.oidc_issuer == _ISSUER

    def test_production_with_only_oidc_configured_boots_and_is_silent(self) -> None:
        """`auth_required=True` with SUPABASE_URL/SECRET both empty must not
        be fatal, and must not warn, when OIDC_ISSUER + OIDC_AUDIENCE are
        the configured verifier."""
        settings = Settings(
            database_url="postgresql+asyncpg://appuser:s3cr3t@db.internal:5432/slm",
            environment="production",
            minio_access_key="real-key",
            minio_secret_key="real-secret",
            auth_required=True,
            supabase_url="",
            supabase_jwt_secret="",
            oidc_issuer=_ISSUER,
            oidc_audience=_AUD,
            openrouter_api_key="sk-or-x",
        )
        assert settings.environment == "production"
        assert settings.startup_warnings() == []

    def test_production_auth_required_with_nothing_configured_is_still_fatal(self) -> None:
        with pytest.raises(Exception, match="OIDC_ISSUER"):
            Settings(
                database_url="postgresql+asyncpg://appuser:s3cr3t@db.internal:5432/slm",
                environment="production",
                minio_access_key="real-key",
                minio_secret_key="real-secret",
                auth_required=True,
                supabase_url="",
                supabase_jwt_secret="",
                oidc_issuer="",
                oidc_audience="",
            )
