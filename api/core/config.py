"""Application settings — single source of truth for all runtime config.

Backed by pydantic-settings; reads `.env` in dev, real env in containers.
Use `get_settings()` (cached) — never re-instantiate `Settings` ad hoc.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import AnyUrl, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Credential values shipped in `.env.example`. `cp .env.example .env` is the
# documented Quickstart step, so these are what a deployment that never
# changed anything is running on — the exact thing the P0 acceptance
# criterion ("ระบบไม่สามารถเริ่มด้วย credential เริ่มต้น") is about.
_DEFAULT_MINIO_CREDENTIAL = "minioadmin"
_DEFAULT_DB_CREDENTIALS = ("slm", "slm")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- App ---------------------------------------------------------------
    # Gates the credential checks below. `dev` is the default on purpose: the
    # Quickstart is `cp .env.example .env`, and the unit suite constructs
    # Settings with nothing but DATABASE_URL set. Enforcing unconditionally
    # would break both, so the deployment has to *say* it is production
    # before production rules apply.
    environment: Literal["dev", "staging", "production"] = "dev"
    log_level: str = Field(default="INFO")
    api_port: int = Field(default=8000, ge=1, le=65535)
    # NoDecode disables pydantic-settings' default JSON decoding so the
    # field_validator below can handle plain comma-separated env values.
    # NOTE: setting API_CORS_ORIGINS in the environment/.env REPLACES this
    # entire list — it does not append to it. If you add an origin here for
    # a real deployment (e.g. a demo frontend), make sure .env.example's
    # documented API_CORS_ORIGINS value includes it too, otherwise anyone
    # who does `cp .env.example .env` (the documented Quickstart step)
    # silently loses CORS access for that origin with no error at startup —
    # only a CORS failure in the browser console, later, that's hard to trace.
    api_cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:5173",
            "https://gentle-fine-tuner.lovable.app",
        ],
    )

    @field_validator("api_cors_origins", mode="before")
    @classmethod
    def _split_csv_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    # ---- Database ----------------------------------------------------------
    # Preferred: postgresql+asyncpg://... (used by FastAPI async sessions)
    database_url: str = Field(...)
    # Optional sync URL override for Alembic. If absent, env.py rewrites
    # database_url's `+asyncpg` to `+psycopg2`.
    alembic_database_url: str | None = None

    # ---- Redis -------------------------------------------------------------
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/1"
    celery_result_backend: str = "redis://redis:6379/2"

    # ---- MinIO -------------------------------------------------------------
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_datasets_bucket: str = "datasets"
    minio_models_bucket: str = "models"
    mlflow_s3_bucket: str = "mlflow"
    minio_use_ssl: bool = False

    # ---- MLflow ------------------------------------------------------------
    mlflow_tracking_uri: AnyUrl = Field(default=AnyUrl("http://mlflow:5000"))
    # Browser-facing MLflow base URL used to build user-clickable run links.
    # The tracking URI above uses the in-network hostname ("mlflow"), which a
    # browser cannot resolve; set this to the externally reachable URL (e.g.
    # http://localhost:5000 via SSH tunnel). Falls back to the tracking URI.
    mlflow_public_url: str | None = None
    mlflow_s3_endpoint_url: AnyUrl = Field(default=AnyUrl("http://minio:9000"))

    # ---- OpenRouter (SDG + LLM judge) -------------------------------------
    openrouter_api_key: str = ""
    openrouter_teacher_model: str = "anthropic/claude-3.5-sonnet"
    openrouter_http_referer: str = "http://localhost:8000"
    openrouter_app_title: str = "slm-platform"

    # ---- Training defaults -------------------------------------------------
    default_base_model: str = "unsloth/Llama-3.2-3B-Instruct-bnb-4bit"
    default_hpo_max_trials: int = Field(default=10, ge=2, le=100)

    # ---- Ollama ------------------------------------------------------------
    ollama_base_url: AnyUrl = Field(default=AnyUrl("http://ollama:11434"))

    # ---- LLM Judge ---------------------------------------------------------
    # OpenRouter model id. The judge model is platform-controlled (not user
    # selectable in the UI). Earlier defaults `anthropic/claude-3.5-sonnet`
    # (retired) and `google/gemini-3.1-flash-lite-preview` were superseded.
    llm_judge_model: str = "qwen/qwen3-235b-a22b-2507"

    # ---- Job reconciliation (orphan sweep, see api/services/job_reconcile.py)
    # Minutes of silence (no fresh `job:{task_id}:last` snapshot, and no DB
    # row update as a fallback) before a `pending`/`running` job whose task id
    # is absent from Celery's active set is flipped to `failed`. Must stay
    # comfortably above normal progress-publish cadence to avoid false
    # positives on a healthy but slow-to-report job.
    job_orphan_grace_minutes: int = Field(default=15, ge=1)

    # ---- Auth (Supabase JWT, see api/core/auth.py) -------------------------
    # Project URL, e.g. https://<ref>.supabase.co — the JWKS used to verify
    # tokens lives at f"{supabase_url}/auth/v1/.well-known/jwks.json", and
    # that URL doubles as the expected `iss` claim.
    supabase_url: str = ""
    # Expected `aud` claim. Supabase's default audience for authenticated
    # end users is the literal string "authenticated".
    supabase_jwt_audience: str = "authenticated"
    # Optional HS256 fallback shared secret, used only for Supabase projects
    # still on legacy symmetric signing keys. Leave unset when the project
    # is on the newer asymmetric (JWKS) keys — which is the production path
    # for this app, since the frontend uses the new publishable-key format.
    supabase_jwt_secret: str = ""
    # Phase-1 compatibility switch (see api/core/auth.py). False (default):
    # tokens are verified when present, but a request with no Authorization
    # header is still allowed through as anonymous — required because the
    # current smart-model-tune frontend does not send the header yet. Flip
    # to True once the frontend ships the header, to actually reject
    # unauthenticated requests.
    auth_required: bool = False

    # ---- Production guards -------------------------------------------------

    @model_validator(mode="after")
    def _reject_default_credentials_in_production(self) -> Self:
        """Refuse to boot a production deployment on the documented defaults.

        Only the credentials that are *always* required are fatal. Notably
        `openrouter_api_key` is NOT: SDG is its only consumer, and a
        deployment that just serves inference on already-exported models is a
        legitimate configuration that should not be blocked. It gets a warning
        from `startup_warnings()` instead.

        The Postgres password is not its own setting — it is embedded in
        `database_url` — so it is checked by parsing the DSN rather than by
        reading POSTGRES_PASSWORD, which this process never sees.
        """
        if self.environment != "production":
            return self

        offenders: list[str] = []
        if self.minio_access_key == _DEFAULT_MINIO_CREDENTIAL:
            offenders.append("MINIO_ACCESS_KEY")
        if self.minio_secret_key == _DEFAULT_MINIO_CREDENTIAL:
            offenders.append("MINIO_SECRET_KEY")
        if _dsn_uses_default_credentials(self.database_url):
            offenders.append("DATABASE_URL (still carries the example slm:slm credentials)")

        if offenders:
            raise ValueError(
                "ENVIRONMENT=production but these still hold the values shipped in "
                f".env.example: {', '.join(offenders)}. Set real secrets, or use "
                "ENVIRONMENT=dev/staging if this is not a production deployment."
            )
        return self

    def startup_warnings(self) -> list[str]:
        """Configuration that is legal but worth shouting about at boot.

        Returned rather than logged so the caller can emit them *after*
        `configure_logging()` has run — settings are built before logging is
        configured, so a `log.warning` in here would go out through the root
        handler in a different format, or be swallowed entirely.
        """
        warnings: list[str] = []
        if self.environment != "production":
            return warnings

        if not self.openrouter_api_key:
            warnings.append(
                "running in production with no OPENROUTER_API_KEY — synthetic data "
                "generation will fail at call time. Fine for an inference-only "
                "deployment; a mistake for any other."
            )
        if not self.auth_required:
            warnings.append(
                "running in production with AUTH_REQUIRED=false — every request is "
                "anonymous and every row it creates has owner_id NULL, which fails "
                "closed for everyone once the flag is flipped. This is the phase-1 "
                "rollout state; flip it once the frontend sends Authorization headers."
            )
        if self.auth_required and not self.supabase_url:
            warnings.append(
                "AUTH_REQUIRED=true with no SUPABASE_URL — there is no JWKS to verify "
                "against, so every authenticated request will be rejected."
            )
        return warnings


def _dsn_uses_default_credentials(dsn: str) -> bool:
    """True when `dsn`'s userinfo is still the example `slm:slm` pair.

    Deliberately parses rather than substring-matching: `slm:slm` also appears
    in the default host and database name (`@postgres:5432/slm`), so a naive
    `"slm:slm" in dsn` would be both over- and under-eager depending on the
    DSN's shape.
    """
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(dsn)
    except ValueError:
        return False
    return (parts.username, parts.password) == _DEFAULT_DB_CREDENTIALS


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()  # type: ignore[call-arg]


__all__ = ["Settings", "get_settings"]
