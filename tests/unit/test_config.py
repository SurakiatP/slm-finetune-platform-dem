"""Regression tests for `api.core.config.Settings`.

These guard the `api_cors_origins` CSV-string parsing path. Pydantic v2
attempts JSON-decode of any `list[X]` env value before field validators
run, so a plain CSV like `a,b,c` raised `JSONDecodeError`. The fix is
`Annotated[list[str], NoDecode]`, which disables the JSON pre-pass.
If someone removes the annotation, these tests fail.
"""

from __future__ import annotations

import pytest


# A non-empty database_url is required by Settings; supply one for every test.
_DB_URL = "postgresql+asyncpg://u:p@h/db"


@pytest.fixture(autouse=True)
def _clear_env_and_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    # Strip any host env vars that could shadow the test inputs.
    for key in (
        "API_CORS_ORIGINS",
        "DATABASE_URL",
        "ALEMBIC_DATABASE_URL",
        "OPENROUTER_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", _DB_URL)
    # `Settings()` is lru_cached via `get_settings`; tests instantiate
    # directly so we don't depend on cache state.


def _import_settings():
    # Import lazily so `monkeypatch.setenv` runs before `model_config` reads `.env`.
    from api.core.config import Settings

    # Disable .env loading so the test does not depend on host's .env file.
    Settings.model_config["env_file"] = None  # type: ignore[index]
    return Settings


def test_cors_origins_default_when_unset() -> None:
    Settings = _import_settings()
    s = Settings()
    assert s.api_cors_origins == [
        "http://localhost:3000",
        "http://localhost:5173",
        "https://gentle-fine-tuner.lovable.app",
    ]


def test_cors_origins_csv_single(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_CORS_ORIGINS", "http://example.com")
    Settings = _import_settings()
    s = Settings()
    assert s.api_cors_origins == ["http://example.com"]


def test_cors_origins_csv_multiple(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "API_CORS_ORIGINS",
        "http://a.com,http://b.com,http://c.com",
    )
    Settings = _import_settings()
    s = Settings()
    assert s.api_cors_origins == [
        "http://a.com",
        "http://b.com",
        "http://c.com",
    ]


def test_cors_origins_csv_strips_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_CORS_ORIGINS", " http://a.com , http://b.com ,  ")
    Settings = _import_settings()
    s = Settings()
    assert s.api_cors_origins == ["http://a.com", "http://b.com"]


def test_cors_origins_explicit_list_kwarg() -> None:
    """Passing a Python list directly must still work — the validator
    short-circuits when the value is not a string."""
    Settings = _import_settings()
    s = Settings(api_cors_origins=["http://x"])  # type: ignore[arg-type]
    assert s.api_cors_origins == ["http://x"]
