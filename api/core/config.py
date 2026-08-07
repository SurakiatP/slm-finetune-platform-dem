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
    # Browser-facing base URL for presigned downloads, e.g.
    # https://storage.slmpc.pasaflow.com. `minio_endpoint` above is the
    # in-network hostname ("minio:9000"), which a browser cannot resolve and
    # which may sit behind a port a public edge does not expose — presigned
    # URLs minted against it point nowhere a client can reach. This must be
    # a dedicated subdomain rather than a path prefix on the main host: see
    # the field_validator below for why.
    minio_public_url: str | None = None
    # Presigned GET URL lifetime. Bounded to the range minio-py's `presign_v4`
    # itself hard-rejects `expires` outside of (1s, 7d] — validating here
    # gives a clear pydantic error at boot instead of a stack trace the first
    # time someone tries to mint a link.
    presigned_url_ttl_seconds: int = Field(default=300, ge=60, le=604800)
    # Host header allowlist for Starlette's TrustedHostMiddleware (wired up
    # in a later wave). NoDecode + CSV-splitting validator, same pattern as
    # api_cors_origins above.
    #
    # Default is "*" (allow-all) DELIBERATELY, not an oversight: this unit
    # suite constructs `Settings` with nothing but DATABASE_URL set, and the
    # docker-compose healthcheck curls `localhost` from inside the container.
    # A restrictive default would break both. A production value MUST
    # include `localhost`, `127.0.0.1`, and `api` — omit any of those and
    # the healthcheck itself gets rejected with 400, which looks like the
    # app is down when it is actually the host guard doing its job.
    api_allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["*"])

    @field_validator("api_allowed_hosts", mode="before")
    @classmethod
    def _split_csv_hosts(cls, v: object) -> object:
        """CSV -> list, with **empty meaning allow-all**, not deny-all.

        The empty case is the one that ships. `.env.example` carries a bare
        `API_ALLOWED_HOSTS=` line, and `scripts/deploy_pasaflow_vm.sh` seeds
        the VM's `.env` from it — so "the variable is present but empty" is
        the literal production default, while "the variable is absent" (where
        the field default fires) is only ever the unit suite's shape.

        Returning `[]` here would therefore be catastrophic and silent:
        Starlette computes `allow_any = "*" in allowed_hosts`, so an empty
        list matches no Host at all and `TrustedHostMiddleware` answers 400
        to **every** request — including `/health`, which makes the whole
        stack look dead while it is in fact working perfectly. The field
        default above, this validator, and `.env.example`'s comment all have
        to agree on allow-all, and only this line was disagreeing.
        """
        if isinstance(v, str):
            hosts = [s.strip() for s in v.split(",") if s.strip()]
            return hosts or ["*"]
        return v

    @field_validator("minio_public_url")
    @classmethod
    def _validate_minio_public_url(cls, v: str | None) -> str | None:
        if not v:
            return None
        v = v.strip()
        if not v:
            return None
        v = v.rstrip("/")

        from urllib.parse import urlsplit

        parts = urlsplit(v)
        if parts.scheme not in ("http", "https"):
            raise ValueError(
                f"MINIO_PUBLIC_URL must use http:// or https://, got: {v!r}"
            )
        if parts.path:
            raise ValueError(
                "MINIO_PUBLIC_URL must not have a path component "
                f"(got path {parts.path!r} in {v!r}). SigV4 signs the "
                "canonical URI, so a path prefix would require the edge "
                "proxy to rewrite the path on the way to MinIO — which "
                "invalidates the signature. Use a dedicated storage "
                "subdomain (e.g. https://storage.example.com) instead of a "
                "path prefix on the main host."
            )
        # presign_v4 signs `host:` + `url.netloc` verbatim (minio/signer.py:275),
        # but a browser omits a scheme's default port when it sends the
        # request — so a URL that spells out :443 on https (or :80 on http)
        # would sign a Host header the browser never actually sends, and the
        # signature would never match. A non-default port (e.g. :9000) is
        # fine and required for that exact reason: it will always be sent.
        if (parts.scheme == "https" and parts.port == 443) or (
            parts.scheme == "http" and parts.port == 80
        ):
            raise ValueError(
                f"MINIO_PUBLIC_URL must not spell out the default port for "
                f"its scheme (got {v!r}). presign_v4 signs the host header "
                "verbatim including the port, but browsers omit a default "
                "port when sending the request, so the signature would "
                "never match. Omit the port instead (or use a non-default "
                "port, which is safe)."
            )
        return v

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

    # ---- Model pricing (see api/services/model_pricing.py) -----------------
    # JSON object of {model_id: {"prompt": usd_per_1m, "completion": usd_per_1m}}
    # overriding/extending the built-in price map. Typed `str`, not `dict`,
    # on purpose — a `dict[str, ...]` field would hit the same
    # pydantic-settings JSON-pre-decode surprise documented on
    # `api_cors_origins` above. The consuming module is responsible for
    # parsing this string itself.
    model_pricing_json: str = ""

    # ---- Concurrency quotas (see api/services/quota.py) ---------------------
    # Per-actor caps apply to authenticated callers only — ownership is
    # tracked via `Project.owner_id`, and the DB has no IP address to bucket
    # anonymous callers on. Under today's AUTH_REQUIRED=false this means the
    # per-actor limit is inert and only the global cap is enforced; that is
    # deliberate, and it stays that way until AUTH_REQUIRED flips. Do not
    # paper over the gap with a Redis-per-IP side channel.
    quota_max_gpu_jobs_per_actor: int = Field(default=1, ge=1)
    quota_max_sdg_jobs_per_actor: int = Field(default=2, ge=1)
    quota_max_gpu_jobs_global: int = Field(default=4, ge=1)
    quota_max_sdg_jobs_global: int = Field(default=8, ge=1)
    # Seconds a quota-rejected request's `Retry-After` header advises waiting.
    quota_retry_after_seconds: int = Field(default=30, ge=1)

    # ---- OpenRouter circuit breaker (see api/services/circuit_breaker.py) --
    openrouter_breaker_failure_threshold: int = Field(default=5, ge=1)
    openrouter_breaker_open_seconds: int = Field(default=60, ge=1)

    # ---- Monthly OpenRouter budget ------------------------------------------
    # The reset window is the *calendar* month, matching OpenRouter's own
    # billing period. Both caps default to `None`, meaning unlimited — the
    # feature ships dark, and a deployment opts in by setting a real number
    # rather than the other way around.
    budget_monthly_usd_per_actor: float | None = Field(default=None, ge=0)
    budget_monthly_usd_global: float | None = Field(default=None, ge=0)

    # ---- Production guards -------------------------------------------------

    @model_validator(mode="after")
    def _reject_unsafe_production_config(self) -> Self:
        """Refuse to boot a production deployment on unsafe configuration.

        Covers two shapes of unsafe: still-default credentials shipped in
        `.env.example`, and settings combinations that are internally legal
        but guarantee bad outcomes for every request (anonymous writes,
        auth that can never verify anything).

        This MUST stay a single `@model_validator`, not split by concern:
        pydantic stops at the first `raise`, so a second validator would
        only ever report whichever offender category it owns, hiding the
        rest. `test_the_error_names_every_offender_at_once` encodes that one
        boot must produce one complete list — keep it that way when adding
        new offenders below.

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
        if self.alembic_database_url is not None and _dsn_uses_default_credentials(
            self.alembic_database_url
        ):
            offenders.append(
                "ALEMBIC_DATABASE_URL (still carries the example slm:slm credentials)"
            )
        if not self.auth_required:
            offenders.append(
                "AUTH_REQUIRED=false — every request would be anonymous and every row "
                "it creates would have owner_id NULL. Back-fill existing rows with "
                "scripts/backfill_project_owner.py, ship the frontend Authorization "
                "header, then set AUTH_REQUIRED=true."
            )
        if self.auth_required and not self.supabase_url and not self.supabase_jwt_secret:
            # Neither the JWKS path (supabase_url) nor the HS256 fallback
            # (supabase_jwt_secret) is configured, which means there is no
            # material to verify a token against at all — a guaranteed 100%
            # rejection rate for every authenticated request, not a
            # degraded-but-working state. `SUPABASE_JWT_SECRET` ships blank
            # in .env.example, so unlike the MinIO/DB credentials above
            # there is no default *value* to compare against here — an
            # empty string is simply "unset".
            offenders.append(
                "AUTH_REQUIRED=true with neither SUPABASE_URL nor SUPABASE_JWT_SECRET "
                "set — there is no JWKS and no HS256 fallback to verify tokens "
                "against, so every authenticated request would be rejected."
            )

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
        # No `auth_required=false` branch here: that combination is now
        # fatal in `_reject_unsafe_production_config` above, so this code
        # path is unreachable in production and would be dead-code that
        # lies about being a warning.
        if self.auth_required and not self.supabase_url and self.supabase_jwt_secret:
            # This is still only a warning, not fatal: supabase_jwt_secret
            # being set means the HS256 fallback path has what it needs to
            # verify tokens even with no SUPABASE_URL. The fatal case (no
            # url AND no secret) is handled above.
            warnings.append(
                "AUTH_REQUIRED=true with no SUPABASE_URL — falling back to the "
                "HS256 SUPABASE_JWT_SECRET path. Fine for legacy Supabase "
                "projects still on symmetric signing keys; a mistake otherwise."
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
