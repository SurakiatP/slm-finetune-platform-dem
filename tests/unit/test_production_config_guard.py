"""A production deployment must not boot on the values from `.env.example`.

BACKEND_GAP_ANALYSIS.md P0: "ห้ามใช้ credential ค่าเริ่มต้นใน production และให้
ระบบ start ไม่ได้เมื่อ secret ที่จำเป็นหายไป", with the acceptance criterion
"ระบบไม่สามารถเริ่มด้วย credential เริ่มต้น".

The gate is keyed on `ENVIRONMENT=production` rather than applied
unconditionally, and that is not timidity — the documented Quickstart is
`cp .env.example .env`, and this very test suite builds `Settings` with
nothing but `DATABASE_URL` set. An unconditional check would break both, so
the deployment has to declare itself production before production rules bite.

The parametrized shape matches the house guards elsewhere in this suite
(`test_worker_progress_frames._CANCELLABLE_TASKS`): adding a credential to the
dict extends every assertion at once.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.core.config import Settings, _dsn_uses_default_credentials

# A DSN with credentials that are NOT the shipped defaults.
SAFE_DSN = "postgresql+asyncpg://appuser:s3cr3t@db.internal:5432/slm"
# Exactly what `.env.example` + docker-compose.yml produce together.
DEFAULT_DSN = "postgresql+asyncpg://slm:slm@postgres:5432/slm"

SAFE_MINIO = {"minio_access_key": "real-key", "minio_secret_key": "real-secret"}
# A SUPABASE_URL that is not the shipped-blank default, so `_prod()`'s base
# case has real auth verification material and boots cleanly.
SAFE_SUPABASE_URL = "https://ref.supabase.co"


def _prod(**overrides) -> Settings:
    # auth_required + supabase_url are part of the SAFE baseline now, not
    # just the credential fields: AUTH_REQUIRED=false and "auth_required
    # with no verification material" are both fatal in production as of
    # this guard (see TestDefaultCredentialsRefuseToBoot / TestAuthMaterial
    # below). Every `_prod(...)` call in this file relies on this baseline
    # being safe by default so it can override just the one field under
    # test — a caller that wants to exercise the auth-fatal paths overrides
    # `auth_required` / `supabase_url` / `supabase_jwt_secret` explicitly.
    base = {
        "environment": "production",
        "database_url": SAFE_DSN,
        "auth_required": True,
        "supabase_url": SAFE_SUPABASE_URL,
        **SAFE_MINIO,
    }
    return Settings(**{**base, **overrides})


# =============================================================================
# 1. The gate itself
# =============================================================================


class TestGateIsScopedToProduction:
    @pytest.mark.parametrize("env", ["dev", "staging"])
    def test_defaults_are_fine_outside_production(self, env: str) -> None:
        """`cp .env.example .env` must keep working for local development —
        that is the documented Quickstart."""
        settings = Settings(environment=env, database_url=DEFAULT_DSN)
        assert settings.minio_access_key == "minioadmin"

    def test_dev_is_the_default_environment(self) -> None:
        """A deployment has to opt in to production. Defaulting the other way
        would fail this suite at collection, since conftest sets only
        DATABASE_URL."""
        assert Settings(database_url=SAFE_DSN).environment == "dev"

    def test_an_unknown_environment_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Settings(environment="prod", database_url=SAFE_DSN)


# =============================================================================
# 2. What is fatal in production
# =============================================================================


_FATAL = {
    "minio_access_key": {"minio_access_key": "minioadmin"},
    "minio_secret_key": {"minio_secret_key": "minioadmin"},
    "database_url": {"database_url": DEFAULT_DSN},
    "alembic_database_url": {"alembic_database_url": DEFAULT_DSN},
    "auth_required": {"auth_required": False},
}


class TestDefaultCredentialsRefuseToBoot:
    @pytest.mark.parametrize("name", sorted(_FATAL))
    def test_each_default_credential_blocks_startup(self, name: str) -> None:
        with pytest.raises(ValidationError) as exc:
            _prod(**_FATAL[name])
        assert "production" in str(exc.value)

    def test_the_error_names_every_offender_at_once(self) -> None:
        """One boot, one complete list. Reporting them one at a time turns
        remediation into three deploy cycles."""
        with pytest.raises(ValidationError) as exc:
            Settings(environment="production", database_url=DEFAULT_DSN)
        message = str(exc.value)
        assert "MINIO_ACCESS_KEY" in message
        assert "MINIO_SECRET_KEY" in message
        assert "DATABASE_URL" in message

    def test_the_error_says_how_to_proceed(self) -> None:
        """An operator hitting this at 3am needs the fix in the message, not
        a grep through config.py."""
        with pytest.raises(ValidationError) as exc:
            _prod(minio_secret_key="minioadmin")
        assert "ENVIRONMENT=dev" in str(exc.value)

    def test_real_credentials_boot_cleanly(self) -> None:
        assert _prod().environment == "production"

    def test_alembic_dsn_with_default_credentials_blocks_startup(self) -> None:
        """`alembic_database_url` is a separate field from `database_url` —
        a deployment could fix the async DSN and forget the sync override
        Alembic reads, since it is optional and easy to leave stale."""
        with pytest.raises(ValidationError) as exc:
            _prod(alembic_database_url=DEFAULT_DSN)
        assert "ALEMBIC_DATABASE_URL" in str(exc.value)

    def test_auth_required_false_now_blocks_startup(self) -> None:
        """This used to be `test_auth_disabled_in_production_is_a_warning_not_a_failure`,
        which argued *for* the warning: failing here would block deploying
        production at all until the frontend shipped Authorization headers.
        That tradeoff has been resolved the other way — anonymous writes
        with owner_id NULL are exactly the failure mode this guard exists to
        catch, so blocking startup is now precisely the point, not a
        regression to avoid."""
        with pytest.raises(ValidationError) as exc:
            _prod(auth_required=False)
        message = str(exc.value)
        assert "AUTH_REQUIRED" in message
        assert "owner_id NULL" in message


class TestDsnCredentialDetection:
    @pytest.mark.parametrize(
        ("dsn", "is_default"),
        [
            (DEFAULT_DSN, True),
            ("postgresql://slm:slm@localhost/slm", True),
            (SAFE_DSN, False),
            # The host and database name are also literally "slm" — a
            # substring check for "slm:slm" would be fooled by neither of
            # these, but parsing keeps that guarantee explicit.
            ("postgresql+asyncpg://slm:realpw@postgres:5432/slm", False),
            ("postgresql+asyncpg://other:slm@postgres:5432/slm", False),
            ("", False),
            ("not-a-dsn", False),
        ],
    )
    def test_only_the_exact_default_pair_matches(self, dsn: str, is_default: bool) -> None:
        assert _dsn_uses_default_credentials(dsn) is is_default

    def test_a_malformed_dsn_does_not_raise(self) -> None:
        """A DSN this function cannot parse is the database layer's problem to
        report, not a reason for the config guard to crash the boot with a
        confusing error."""
        assert _dsn_uses_default_credentials("://:::") is False


# =============================================================================
# 3. What is only a warning
# =============================================================================


class TestWarningsDoNotBlockStartup:
    def test_missing_openrouter_key_is_a_warning_not_a_failure(self) -> None:
        """SDG is the only consumer. A deployment that serves inference on
        already-exported models is legitimate and must not be blocked."""
        settings = _prod(openrouter_api_key="")
        assert any("OPENROUTER_API_KEY" in w for w in settings.startup_warnings())

    # `test_auth_disabled_in_production_is_a_warning_not_a_failure` used to
    # live here. AUTH_REQUIRED=false in production is fatal now — see
    # `TestDefaultCredentialsRefuseToBoot.test_auth_required_false_now_blocks_startup`
    # above — so there is no warning-only case for it left to test here.

    def test_a_fully_configured_production_is_silent(self) -> None:
        settings = _prod(
            openrouter_api_key="sk-or-x",
            auth_required=True,
            supabase_url="https://ref.supabase.co",
        )
        assert settings.startup_warnings() == []

    @pytest.mark.parametrize("env", ["dev", "staging"])
    def test_no_warnings_outside_production(self, env: str) -> None:
        """Local dev has no OpenRouter key and no auth by design; warning
        about it every boot is how operators learn to ignore warnings."""
        settings = Settings(environment=env, database_url=DEFAULT_DSN)
        assert settings.startup_warnings() == []


# =============================================================================
# 4. Auth verification material — fatal vs. warning-only
# =============================================================================


class TestAuthMaterial:
    """AUTH_REQUIRED=true needs *something* to verify a token against: either
    SUPABASE_URL (JWKS / asymmetric keys) or SUPABASE_JWT_SECRET (HS256
    fallback for legacy Supabase projects). Neither present is fatal — a
    guaranteed 100% rejection rate is not a "legal but risky" config, it is
    broken. Missing just SUPABASE_URL while the HS256 secret IS set is the
    neighbouring case a naive "require supabase_url" implementation gets
    wrong: that combination still verifies tokens, so it must stay a
    warning, not become fatal.
    """

    def test_neither_url_nor_secret_is_fatal(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _prod(auth_required=True, supabase_url="", supabase_jwt_secret="")
        message = str(exc.value)
        assert "SUPABASE_URL" in message
        assert "SUPABASE_JWT_SECRET" in message

    def test_secret_without_url_is_only_a_warning(self) -> None:
        """The HS256 fallback path needs only the secret — SUPABASE_URL is
        for the JWKS/asymmetric path and is not required alongside it."""
        settings = _prod(auth_required=True, supabase_url="", supabase_jwt_secret="hs256-secret")
        assert settings.environment == "production"  # did not raise
        assert any("SUPABASE_URL" in w for w in settings.startup_warnings())

    def test_url_without_secret_boots_silently(self) -> None:
        """The baseline `_prod()` shape: SUPABASE_URL set, no HS256 secret
        needed. Must be neither fatal nor a warning."""
        settings = _prod(
            auth_required=True,
            supabase_url=SAFE_SUPABASE_URL,
            supabase_jwt_secret="",
            openrouter_api_key="sk-or-x",
        )
        assert settings.startup_warnings() == []


# =============================================================================
# 5. `minio_public_url` validation
# =============================================================================


class TestMinioPublicUrl:
    def test_none_is_accepted(self) -> None:
        assert Settings(database_url=SAFE_DSN, minio_public_url=None).minio_public_url is None

    def test_a_path_component_is_rejected(self) -> None:
        """SigV4 signs the canonical URI. A path prefix would force the edge
        proxy to rewrite the path on the way to MinIO, which invalidates the
        signature — hence the dedicated storage subdomain requirement."""
        with pytest.raises(ValidationError) as exc:
            Settings(database_url=SAFE_DSN, minio_public_url="https://host/storage")
        assert "path" in str(exc.value).lower()

    def test_explicit_default_https_port_is_rejected(self) -> None:
        """`presign_v4` signs `host:` + `url.netloc` verbatim, but a browser
        omits the default port when it sends the request — an explicit :443
        would sign a Host header that never arrives, so it can never match."""
        with pytest.raises(ValidationError) as exc:
            Settings(database_url=SAFE_DSN, minio_public_url="https://host:443")
        assert "port" in str(exc.value).lower()

    def test_explicit_default_http_port_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc:
            Settings(database_url=SAFE_DSN, minio_public_url="http://host:80")
        assert "port" in str(exc.value).lower()

    def test_non_default_port_is_accepted(self) -> None:
        settings = Settings(database_url=SAFE_DSN, minio_public_url="https://host:9000")
        assert settings.minio_public_url == "https://host:9000"

    def test_bare_host_with_no_port_is_accepted(self) -> None:
        settings = Settings(database_url=SAFE_DSN, minio_public_url="https://host")
        assert settings.minio_public_url == "https://host"

    def test_trailing_slash_is_stripped(self) -> None:
        settings = Settings(database_url=SAFE_DSN, minio_public_url="https://host/")
        assert settings.minio_public_url == "https://host"


# =============================================================================
# 6. The warnings actually reach a log
# =============================================================================


def test_both_entrypoints_emit_the_warnings() -> None:
    """`startup_warnings()` returns rather than logs, so that the caller can
    emit after `configure_logging()`. That only helps if both processes
    actually call it — the API for the auth/tenancy warnings, and the worker
    because it is the process that calls OpenRouter."""
    from pathlib import Path

    import api.main as api_main
    import workers.celery_app as celery_app

    for module in (api_main, celery_app):
        src = Path(module.__file__).read_text(encoding="utf-8")
        assert "startup_warnings()" in src, f"{module.__name__} never emits config warnings"


class TestApiAllowedHostsEmptyMeansAllowAll:
    """The case that actually ships, which the first draft never tested.

    `TrustedHostMiddleware` is wired to `settings.api_allowed_hosts`
    (`api/main.py`). Starlette computes `allow_any = "*" in allowed_hosts`,
    so an empty list matches nothing and answers **400 to every request** --
    `/health` included, which reads as a dead stack rather than a config
    problem.

    Every other test in this repo builds `Settings` with the variable
    *absent*, where the field default `["*"]` fires and everything looks
    fine. But `.env.example` ships a bare `API_ALLOWED_HOSTS=` line and
    `scripts/deploy_pasaflow_vm.sh` seeds the VM's `.env` from it, so
    "present but empty" is the production default and "absent" is only ever
    the unit suite's shape. Textbook "the assertion sits where it passes":
    the guard was in the branch that held and missing from its neighbour.
    """

    def test_an_empty_env_var_allows_all_rather_than_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("API_ALLOWED_HOSTS", "")
        assert Settings(database_url=DEFAULT_DSN).api_allowed_hosts == ["*"]

    def test_a_whitespace_only_value_also_allows_all(self) -> None:
        """`API_ALLOWED_HOSTS=  ,  ` is a typo, not a deny-all instruction."""
        assert Settings(database_url=DEFAULT_DSN, api_allowed_hosts="  ,  ").api_allowed_hosts == ["*"]

    def test_a_real_list_is_still_honoured(self) -> None:
        """The neighbouring case: fixing the empty case must not turn the
        setting into a no-op that always allows everything."""
        s = Settings(database_url=DEFAULT_DSN, api_allowed_hosts="slmpc.pasaflow.com, localhost")
        assert s.api_allowed_hosts == ["slmpc.pasaflow.com", "localhost"]
        assert "*" not in s.api_allowed_hosts

    def test_the_value_env_example_actually_ships_serves_health(self) -> None:
        """Ties the config file to real behaviour, which is where this broke.

        Reads `API_ALLOWED_HOSTS` out of `.env.example` verbatim, feeds it to
        `Settings`, and drives `TrustedHostMiddleware` with the result. A
        parse-level assertion alone would not have caught the original bug's
        consequence; this asserts the shipped value produces a served
        request rather than a 400.
        """
        from pathlib import Path

        from starlette.applications import Starlette
        from starlette.middleware.trustedhost import TrustedHostMiddleware
        from starlette.responses import PlainTextResponse
        from starlette.routing import Route
        from starlette.testclient import TestClient

        env_example = (Path(__file__).resolve().parents[2] / ".env.example").read_text()
        shipped = next(
            (
                line.split("=", 1)[1]
                for line in env_example.splitlines()
                if line.startswith("API_ALLOWED_HOSTS=")
            ),
            None,
        )
        assert shipped is not None, ".env.example no longer declares API_ALLOWED_HOSTS"

        hosts = Settings(database_url=DEFAULT_DSN, api_allowed_hosts=shipped).api_allowed_hosts
        app = Starlette(routes=[Route("/health", lambda r: PlainTextResponse("ok"))])
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

        # The three Hosts a real deployment must answer: the deploy script's
        # Phase 7 curl, in-network probes, and the public hostname. (There is
        # deliberately no compose `healthcheck:` on `api` or `edge` — only
        # postgres/redis/minio/mlflow define one. Earlier revisions of this
        # comment cited one that does not exist.)
        for host in ("localhost", "api", "slmpc.pasaflow.com"):
            r = TestClient(app, base_url=f"http://{host}").get("/health")
            assert r.status_code == 200, (
                f"the API_ALLOWED_HOSTS value shipped in .env.example rejects "
                f"Host: {host} with {r.status_code}. Every request, including "
                "the deploy script's own health check, would 400 on a fresh deploy."
            )
