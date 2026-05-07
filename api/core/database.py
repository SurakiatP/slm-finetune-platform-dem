"""Async SQLAlchemy 2.0 engine + session factory.

Used by FastAPI dependencies (`get_db`) and any service that needs a session.
The sync side (Alembic) lives in `alembic/env.py` and uses psycopg2.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from api.core.config import get_settings

_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields a transactional session."""
    async with AsyncSessionLocal() as session:
        yield session


__all__ = ["engine", "AsyncSessionLocal", "get_db"]
