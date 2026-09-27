"""Unit tests for `api/services/api_keys_service.py` + `api/routers/api_keys.py` (T5).

Security invariants this file exists to pin down:
  * the plaintext key is only ever present on the `ApiKeyCreatedResponse` of
    the call that created it — never stored, never returned again;
  * only the SHA-256 hash is persisted, and lookup is by that hash;
  * a bad/unknown/revoked key always gets the same generic 401 detail
    (`authenticate` is the auth boundary for `/inference/*` key calls).

`api/routers/api_keys.py` is not mounted in `api/main.py` yet (a later wave
does that), so the HTTP-level tests build a local `FastAPI()` app around the
router instead of importing `api.main.app`.

In-memory aiosqlite only — no Postgres.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from api.core import request_context
from api.core.auth import CurrentUser, require_authenticated_user
from api.core.database import get_db
from api.models.api_key import ApiKey
from api.models.audit_event import AuditEvent
from api.models.base import Base
from api.routers import api_keys as api_keys_router
from api.schemas.api_keys import ApiKeyCreate
from api.services import api_keys_service


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):
    return "JSON"


USER_A = CurrentUser(id="user-a-sub", email="a@example.com")
USER_B = CurrentUser(id="user-b-sub", email="b@example.com")


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


# =============================================================================
# 1. generate() — shape only, no DB
# =============================================================================


def test_generate_shape() -> None:
    plaintext, digest = api_keys_service.generate()
    assert plaintext.startswith(api_keys_service.KEY_PREFIX)
    assert len(digest) == 64
    assert digest == hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


# =============================================================================
# 2. create_key
# =============================================================================


class TestCreateKey:
    async def test_returns_plaintext_once_persists_hash_only(self, db) -> None:
        resp = await api_keys_service.create_key(db, ApiKeyCreate(name="my key"), USER_A)

        assert resp.key.startswith(api_keys_service.KEY_PREFIX)
        assert resp.status == "active"
        assert resp.prefix == resp.key[: len(api_keys_service.KEY_PREFIX) + 4]
        assert resp.last4 == resp.key[-4:]

        row = await db.get(ApiKey, resp.id)
        assert row is not None
        assert row.key_hash != resp.key
        assert row.key_hash == hashlib.sha256(resp.key.encode("utf-8")).hexdigest()

    async def test_cap_reached_is_409(self, db, monkeypatch) -> None:
        monkeypatch.setattr(
            api_keys_service, "get_settings", lambda: SimpleNamespace(api_keys_max_per_user=1)
        )
        await api_keys_service.create_key(db, ApiKeyCreate(name="k1"), USER_A)
        with pytest.raises(HTTPException) as exc:
            await api_keys_service.create_key(db, ApiKeyCreate(name="k2"), USER_A)
        assert exc.value.status_code == 409

    async def test_revoked_keys_dont_count_against_the_cap(self, db, monkeypatch) -> None:
        monkeypatch.setattr(
            api_keys_service, "get_settings", lambda: SimpleNamespace(api_keys_max_per_user=1)
        )
        first = await api_keys_service.create_key(db, ApiKeyCreate(name="k1"), USER_A)
        await api_keys_service.revoke_key(db, first.id, USER_A)
        second = await api_keys_service.create_key(db, ApiKeyCreate(name="k2"), USER_A)
        assert second.status == "active"

    async def test_cap_is_per_owner(self, db, monkeypatch) -> None:
        monkeypatch.setattr(
            api_keys_service, "get_settings", lambda: SimpleNamespace(api_keys_max_per_user=1)
        )
        await api_keys_service.create_key(db, ApiKeyCreate(name="k1"), USER_A)
        # USER_B has their own, independent cap.
        second = await api_keys_service.create_key(db, ApiKeyCreate(name="k1"), USER_B)
        assert second.status == "active"


# =============================================================================
# 3. list_keys
# =============================================================================


class TestListKeys:
    async def test_newest_first_includes_revoked(self, db) -> None:
        first = await api_keys_service.create_key(db, ApiKeyCreate(name="k1"), USER_A)
        second = await api_keys_service.create_key(db, ApiKeyCreate(name="k2"), USER_A)
        # sqlite's CURRENT_TIMESTAMP is second-granularity, so two keys
        # created back-to-back in the same test can tie on `created_at` —
        # force them apart so the ordering assertion below isn't flaky.
        first_row = await db.get(ApiKey, first.id)
        first_row.created_at = first_row.created_at - timedelta(seconds=5)
        await db.commit()
        await api_keys_service.revoke_key(db, first.id, USER_A)

        page = await api_keys_service.list_keys(db, USER_A, limit=50, offset=0)
        assert page.total == 2
        assert [item.id for item in page.items] == [second.id, first.id]
        assert page.items[0].status == "active"
        assert page.items[1].status == "revoked"
        # Never re-exposes the plaintext.
        assert not hasattr(page.items[0], "key")

    async def test_scoped_to_owner(self, db) -> None:
        await api_keys_service.create_key(db, ApiKeyCreate(name="mine"), USER_A)
        await api_keys_service.create_key(db, ApiKeyCreate(name="theirs"), USER_B)
        page = await api_keys_service.list_keys(db, USER_A, limit=50, offset=0)
        assert len(page.items) == 1
        assert page.items[0].name == "mine"


# =============================================================================
# 4. revoke_key
# =============================================================================


class TestRevokeKey:
    async def test_404_when_missing(self, db) -> None:
        with pytest.raises(HTTPException) as exc:
            await api_keys_service.revoke_key(db, uuid4(), USER_A)
        assert exc.value.status_code == 404

    async def test_403_when_not_owner(self, db) -> None:
        created = await api_keys_service.create_key(db, ApiKeyCreate(name="k"), USER_A)
        with pytest.raises(HTTPException) as exc:
            await api_keys_service.revoke_key(db, created.id, USER_B)
        assert exc.value.status_code == 403
        # Still revocable by its actual owner afterwards.
        await api_keys_service.revoke_key(db, created.id, USER_A)
        row = await db.get(ApiKey, created.id)
        assert row.revoked_at is not None

    async def test_idempotent_no_duplicate_audit_row(self, db) -> None:
        created = await api_keys_service.create_key(db, ApiKeyCreate(name="k"), USER_A)
        await api_keys_service.revoke_key(db, created.id, USER_A)
        first_revoked_at = (await db.get(ApiKey, created.id)).revoked_at

        await api_keys_service.revoke_key(db, created.id, USER_A)  # no-op

        row = await db.get(ApiKey, created.id)
        assert row.revoked_at == first_revoked_at
        count = (
            await db.execute(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "api_key.revoke")
            )
        ).scalar_one()
        assert count == 1


# =============================================================================
# 5. authenticate — the auth boundary for key-authenticated inference
# =============================================================================


class TestAuthenticate:
    async def test_valid_key_resolves_owner_and_binds_request_context(self, db) -> None:
        created = await api_keys_service.create_key(db, ApiKeyCreate(name="k"), USER_A)
        request_context.clear()

        row = await api_keys_service.authenticate(db, created.key)

        assert row.owner_id == USER_A.id
        assert request_context.current_user_id() == USER_A.id
        request_context.clear()

    async def test_unknown_and_revoked_keys_get_the_same_401(self, db) -> None:
        created = await api_keys_service.create_key(db, ApiKeyCreate(name="k"), USER_A)
        await api_keys_service.revoke_key(db, created.id, USER_A)

        with pytest.raises(HTTPException) as unknown:
            await api_keys_service.authenticate(db, "sk-slm-does-not-exist")
        with pytest.raises(HTTPException) as revoked:
            await api_keys_service.authenticate(db, created.key)

        assert unknown.value.status_code == revoked.value.status_code == 401
        assert unknown.value.detail == revoked.value.detail == "invalid authentication token"

    async def test_last_used_at_set_on_first_use(self, db) -> None:
        created = await api_keys_service.create_key(db, ApiKeyCreate(name="k"), USER_A)
        row = await api_keys_service.authenticate(db, created.key)
        assert row.last_used_at is not None

    async def test_last_used_at_not_bumped_within_refresh_window(self, db) -> None:
        created = await api_keys_service.create_key(db, ApiKeyCreate(name="k"), USER_A)
        await api_keys_service.authenticate(db, created.key)
        first = (await db.get(ApiKey, created.id)).last_used_at

        await api_keys_service.authenticate(db, created.key)
        second = (await db.get(ApiKey, created.id)).last_used_at

        assert second == first

    async def test_last_used_at_bumped_after_stale_window(self, db) -> None:
        created = await api_keys_service.create_key(db, ApiKeyCreate(name="k"), USER_A)
        await api_keys_service.authenticate(db, created.key)
        row = await db.get(ApiKey, created.id)
        # Assign a known-aware timestamp directly rather than subtracting
        # from `row.last_used_at` — sqlite (unlike Postgres) round-trips
        # `DateTime(timezone=True)` as naive, so reusing that value here
        # would make the tzinfo of `stale` an accident of GC timing.
        stale = datetime.now(UTC) - timedelta(seconds=120)
        row.last_used_at = stale
        await db.commit()

        await api_keys_service.authenticate(db, created.key)
        refreshed = (await db.get(ApiKey, created.id)).last_used_at
        assert refreshed > stale


# =============================================================================
# 6. Router wiring over real HTTP (local app — not mounted in api.main yet)
# =============================================================================


@pytest.fixture
def client_and_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    import asyncio

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_setup())

    async def _get_db():
        async with maker() as session:
            yield session

    app = FastAPI()
    app.include_router(api_keys_router.router, prefix="/api/v1/api-keys")
    app.dependency_overrides[get_db] = _get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


class TestRouterOverHttp:
    def test_missing_token_is_401(self, client_and_db) -> None:
        client = client_and_db
        resp = client.post("/api/v1/api-keys", json={"name": "k"})
        assert resp.status_code == 401
        assert resp.json()["detail"] == "authentication required"

    def test_create_list_revoke_round_trip(self, client_and_db) -> None:
        client = client_and_db
        app = client.app
        app.dependency_overrides[require_authenticated_user] = lambda: USER_A

        created = client.post("/api/v1/api-keys", json={"name": "ci key"})
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["key"].startswith(api_keys_service.KEY_PREFIX)
        key_id = body["id"]

        listed = client.get("/api/v1/api-keys")
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert "key" not in listed.json()["items"][0]

        revoked = client.delete(f"/api/v1/api-keys/{key_id}")
        assert revoked.status_code == 204

        listed_again = client.get("/api/v1/api-keys")
        assert listed_again.json()["items"][0]["status"] == "revoked"

        app.dependency_overrides.pop(require_authenticated_user, None)
