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


def _prod(**overrides) -> Settings:
    base = {"environment": "production", "database_url": SAFE_DSN, **SAFE_MINIO}
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

    def test_auth_disabled_in_production_is_a_warning_not_a_failure(self) -> None:
        """Failing here would block deploying production at all until the
        Lovable frontend ships Authorization headers — work this repo cannot
        do. The warning states the consequence instead."""
        settings = _prod(auth_required=False)
        warning = next(w for w in settings.startup_warnings() if "AUTH_REQUIRED" in w)
        assert "owner_id NULL" in warning

    def test_auth_required_without_supabase_url_is_flagged(self) -> None:
        """A config that rejects every request while looking correct."""
        settings = _prod(auth_required=True, supabase_url="")
        assert any("SUPABASE_URL" in w for w in settings.startup_warnings())

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
# 4. The warnings actually reach a log
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
