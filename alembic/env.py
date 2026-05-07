"""Alembic environment.

Reads the DB URL from `ALEMBIC_DATABASE_URL` (preferred) or `DATABASE_URL`,
auto-converting an asyncpg URL to its psycopg2 equivalent so this script can
stay synchronous (Alembic's online mode plays best with a sync driver).
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make `api`, `workers`, `ai_engine` importable when alembic runs from repo root.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from api.models import Base  # noqa: E402  — must come after sys.path mutation.

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _resolve_db_url() -> str:
    raw = os.getenv("ALEMBIC_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not raw:
        raise RuntimeError(
            "Set ALEMBIC_DATABASE_URL (or DATABASE_URL) before running alembic."
        )
    # Alembic must use a sync driver. Re-route asyncpg/postgresql:// to psycopg2.
    if "+asyncpg" in raw:
        return raw.replace("+asyncpg", "+psycopg2", 1)
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+psycopg2://", 1)
    return raw


config.set_main_option("sqlalchemy.url", _resolve_db_url())

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Generate SQL without connecting to a database."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against a live database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
