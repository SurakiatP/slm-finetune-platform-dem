"""Unit tests for `api/routers/notifications.py` + its service (in-memory sqlite)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from api.core.auth import CurrentUser, require_authenticated_user
from api.core.database import get_db
from api.models.base import Base
from api.models.notification import Notification
from api.routers import notifications as notifications_router

USER_A = CurrentUser(id="user-a-sub", email="a@example.com")
USER_B = CurrentUser(id="user-b-sub", email="b@example.com")
T0 = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):
    return "JSON"


def _row(owner: str, created_at: datetime, read: bool = False) -> Notification:
    return Notification(
        id=uuid4(),
        owner_id=owner,
        event_type="dataset.completed",
        resource_type="dataset",
        resource_id=str(uuid4()),
        payload={"name": "d"},
        created_at=created_at,
        read_at=created_at if read else None,
    )


@pytest.fixture
def env():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_setup())

    async def _get_db():
        async with maker() as session:
            yield session

    def seed(rows: list[Notification]) -> None:
        async def _go() -> None:
            async with maker() as s:
                s.add_all(rows)
                await s.commit()

        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_go())

    app = FastAPI()
    app.include_router(notifications_router.router, prefix="/api/v1/notifications")
    app.dependency_overrides[get_db] = _get_db
    with TestClient(app) as client:
        yield client, seed
    app.dependency_overrides.clear()


def _as(client: TestClient, user: CurrentUser) -> None:
    client.app.dependency_overrides[require_authenticated_user] = lambda: user


def test_no_identity_is_401(env) -> None:
    client, _ = env
    assert client.get("/api/v1/notifications").status_code == 401
    assert client.post("/api/v1/notifications/read-all").status_code == 401


def test_newest_first_and_unread_count(env) -> None:
    client, seed = env
    rows = [_row("user-a-sub", T0 + timedelta(minutes=i), read=(i == 0)) for i in range(3)]
    seed([*rows, _row("user-b-sub", T0)])
    _as(client, USER_A)
    body = client.get("/api/v1/notifications").json()
    assert [i["id"] for i in body["items"]] == [str(r.id) for r in reversed(rows)]
    assert body["unread_count"] == 2
    assert body["next_cursor"] is None


def test_two_page_walk_with_equal_created_at_ties(env) -> None:
    client, seed = env
    rows = (
        [_row("user-a-sub", T0) for _ in range(3)]
        + [_row("user-a-sub", T0 + timedelta(minutes=1)) for _ in range(2)]
        + [_row("user-a-sub", T0 - timedelta(minutes=1))]
    )
    seed(rows)
    _as(client, USER_A)
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        body = client.get("/api/v1/notifications", params=params).json()
        seen += [i["id"] for i in body["items"]]
        assert body["unread_count"] == 6  # constant across pages
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert pages == 3
    assert len(seen) == len(set(seen)) == 6
    assert set(seen) == {str(r.id) for r in rows}


def test_garbage_cursor_is_422(env) -> None:
    client, _ = env
    _as(client, USER_A)
    for bad in ("!!!notbase64", "bm9waXBl"):
        assert client.get("/api/v1/notifications", params={"cursor": bad}).status_code == 422


@pytest.mark.parametrize("limit", [0, 101])
def test_limit_bounds_422(env, limit: int) -> None:
    client, _ = env
    _as(client, USER_A)
    assert client.get("/api/v1/notifications", params={"limit": limit}).status_code == 422


def test_mark_one_is_idempotent_and_owner_scoped(env) -> None:
    client, seed = env
    mine, theirs = _row("user-a-sub", T0), _row("user-b-sub", T0)
    seed([mine, theirs])
    _as(client, USER_A)
    first = client.patch(f"/api/v1/notifications/{mine.id}/read")
    assert first.status_code == 200 and first.json()["read_at"] is not None
    second = client.patch(f"/api/v1/notifications/{mine.id}/read")
    assert second.json()["read_at"] == first.json()["read_at"]
    assert client.patch(f"/api/v1/notifications/{theirs.id}/read").status_code == 404
    assert client.patch(f"/api/v1/notifications/{uuid4()}/read").status_code == 404


def test_read_all_only_marks_callers_rows(env) -> None:
    client, seed = env
    seed(
        [
            _row("user-a-sub", T0),
            _row("user-a-sub", T0 + timedelta(minutes=1)),
            _row("user-a-sub", T0 + timedelta(minutes=2), read=True),
            _row("user-b-sub", T0),
        ]
    )
    _as(client, USER_A)
    assert client.post("/api/v1/notifications/read-all").json() == {"marked_count": 2}
    assert client.get("/api/v1/notifications").json()["unread_count"] == 0
    assert client.post("/api/v1/notifications/read-all").json() == {"marked_count": 0}
    _as(client, USER_B)
    assert client.get("/api/v1/notifications").json()["unread_count"] == 1
