"""Sync SQLAlchemy session for the Celery worker.

Workers run synchronous code, so we keep a parallel sync engine. The async
side lives in `api/core/database.py`. Both share the same metadata
(`api.models.Base`) and Alembic-managed schema.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from api.core.config import get_settings


def _sync_url(url: str) -> str:
    """Rewrite an async DSN to its psycopg2 equivalent."""
    if "+asyncpg" in url:
        return url.replace("+asyncpg", "+psycopg2", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


_settings = get_settings()
sync_engine = create_engine(
    _sync_url(_settings.alembic_database_url or _settings.database_url),
    pool_pre_ping=True,
    pool_size=2,
    max_overflow=2,
)
SyncSessionLocal: sessionmaker[Session] = sessionmaker(
    bind=sync_engine,
    expire_on_commit=False,
)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session context.

    Commits on clean exit; rolls back on exception; always closes.
    """
    session = SyncSessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__ = ["sync_engine", "SyncSessionLocal", "session_scope"]
