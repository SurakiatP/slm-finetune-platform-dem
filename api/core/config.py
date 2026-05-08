"""Application settings — single source of truth for all runtime config.

Backed by pydantic-settings; reads `.env` in dev, real env in containers.
Use `get_settings()` (cached) — never re-instantiate `Settings` ad hoc.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import AnyUrl, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- App ---------------------------------------------------------------
    log_level: str = Field(default="INFO")
    api_port: int = Field(default=8000, ge=1, le=65535)
    # NoDecode disables pydantic-settings' default JSON decoding so the
    # field_validator below can handle plain comma-separated env values.
    api_cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost:5173"],
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
    llm_judge_model: str = "anthropic/claude-3.5-sonnet"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()  # type: ignore[call-arg]


__all__ = ["Settings", "get_settings"]
