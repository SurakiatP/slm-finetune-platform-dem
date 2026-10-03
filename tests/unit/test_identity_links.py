"""Keycloak identities resolve to Engine actors (`identity_links`).

The acceptance table from the frontend team's Keycloak migration doc, run
end to end against the real app (REST + WebSocket) with both providers
trusted at once:

    Supabase token (old frontend)           -> 200, sees the old project
    Keycloak token, identity linked         -> 200, sees the SAME project
    Keycloak token, never linked            -> 200, sees nothing; 403 on it
    WS job of own / someone else's          -> accepted / 4403

Plus the operator script `scripts/link_identity.py`. In-memory aiosqlite,
fakeredis, local RSA keys; no network.
"""

from __future__ import annotations

import io
import time
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from starlette.websockets import WebSocketDisconnect

from api.core import auth as auth_mod
from api.core.config import get_settings
from api.core.database import get_db
from api.main import app
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.identity_link import IdentityLink
from api.models.project import Project
from api.schemas.enums import DatasetSource, JobStatus, TaskType
from api.services.identity_links import resolve_actor
from scripts import link_identity


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):
    return "JSON"


_KC_ISS = "https://tunelab.example.com/auth/realms/tunelab"
_KC_AUD = "tunelab-api"
_KC_JWKS = "http://tunelab-main-keycloak:8080/auth/realms/tunelab/protocol/openid-connect/certs"
_SB_URL = "https://proj.supabase.co"
_SB_ISS = f"{_SB_URL}/auth/v1"
_SB_JWKS = f"{_SB_ISS}/.well-known/jwks.json"

OLD_OWNER = "02fda8bd-d111-4e1d-af2e-121ce36d8559"  # Supabase sub owning legacy rows
KC_LINKED = "kc-sub-linked"
KC_STRANGER = "kc-sub-stranger"
JOB = "job-owned-by-old-owner"


def _rsa():
    from cryptography.hazmat.primitives.asymmetric import rsa

    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


_KEYS = {_KC_JWKS: _rsa(), _SB_JWKS: _rsa()}


def _token(jwks: str, iss: str, aud: str, sub: str) -> str:
    claims = {"sub": sub, "iss": iss, "aud": aud, "exp": int(time.time()) + 3600}
    return jwt.encode(claims, _KEYS[jwks], algorithm="RS256")


def _supabase(sub: str) -> str:
    return _token(_SB_JWKS, _SB_ISS, "authenticated", sub)


def _keycloak(sub: str) -> str:
    return _token(_KC_JWKS, _KC_ISS, _KC_AUD, sub)


@pytest.fixture
def dual_auth(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OIDC_ISSUER", _KC_ISS)
    monkeypatch.setenv("OIDC_AUDIENCE", _KC_AUD)
    monkeypatch.setenv("OIDC_JWKS_URL", _KC_JWKS)
    monkeypatch.setenv("SUPABASE_URL", _SB_URL)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "")
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", "authenticated")
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    get_settings.cache_clear()
    auth_mod._reset_jwks_client_cache()

    class _Client:
        def __init__(self, url: str) -> None:
            self.url = url

        def get_signing_key_from_jwt(self, _token: str):
            class _Key:
                key = _KEYS[self.url].public_key()
                algorithm_name = "RS256"

            return _Key()

    monkeypatch.setattr(auth_mod, "_get_jwks_client", _Client)
    yield
    get_settings.cache_clear()
    auth_mod._reset_jwks_client_cache()


@pytest.fixture
async def db_maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        project = Project(id=uuid4(), name="legacy", task_type=TaskType.QA, owner_id=OLD_OWNER)
        session.add(project)
        session.add(
            Dataset(
                id=uuid4(), project_id=project.id, name="d", owner_id=OLD_OWNER,
                source=DatasetSource.SDG, task_type=TaskType.QA,
                status=JobStatus.RUNNING, celery_task_id=JOB,
            )
        )
        session.add(IdentityLink(issuer=_KC_ISS, subject=KC_LINKED, actor_id=OLD_OWNER))
        await session.commit()
    maker.project_id = project.id  # type: ignore[attr-defined]
    yield maker
    await engine.dispose()


@pytest.fixture
def client(dual_auth, db_maker, monkeypatch: pytest.MonkeyPatch):
    import fakeredis
    import fakeredis.aioredis

    from api.routers import websocket as ws_router

    server = fakeredis.FakeServer()

    def _redis_factory():
        c = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)

        async def _aclose(*a, **kw) -> None:
            return None

        c.aclose = _aclose  # type: ignore[method-assign]
        return c

    monkeypatch.setattr(ws_router, "get_redis_client", _redis_factory)

    async def _override_db():
        async with db_maker() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _projects(client: TestClient, token: str) -> list[str]:
    resp = client.get("/api/v1/projects", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    return [p["name"] for p in resp.json()["items"]]


# =============================================================================
# 1. REST acceptance table
# =============================================================================


class TestRest:
    def test_supabase_token_still_sees_legacy_project(self, client) -> None:
        assert _projects(client, _supabase(OLD_OWNER)) == ["legacy"]

    def test_linked_keycloak_token_sees_the_same_project(self, client) -> None:
        assert _projects(client, _keycloak(KC_LINKED)) == ["legacy"]

    def test_unlinked_keycloak_token_sees_nothing(self, client) -> None:
        assert _projects(client, _keycloak(KC_STRANGER)) == []

    def test_unlinked_keycloak_token_gets_403_on_the_legacy_project(
        self, client, db_maker
    ) -> None:
        resp = client.get(
            f"/api/v1/projects/{db_maker.project_id}",
            headers={"Authorization": f"Bearer {_keycloak(KC_STRANGER)}"},
        )
        assert resp.status_code == 403

    def test_keycloak_sub_equal_to_a_legacy_owner_id_does_not_inherit_it(
        self, client
    ) -> None:
        """Unlinked identities get a server-issued actor id, never their raw
        `sub` — so a Keycloak `sub` that happens to equal a Supabase owner
        id grants nothing."""
        assert _projects(client, _keycloak(OLD_OWNER)) == []

    def test_missing_and_invalid_tokens_401(self, client) -> None:
        assert client.get("/api/v1/projects").status_code == 401
        resp = client.get("/api/v1/projects", headers={"Authorization": "Bearer nope"})
        assert resp.status_code == 401

    def test_new_keycloak_user_owns_what_they_create(self, client) -> None:
        token = _keycloak("kc-sub-newcomer")
        resp = client.post(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "fresh", "task_type": "qa"},
        )
        assert resp.status_code == 201, resp.text
        assert _projects(client, token) == ["fresh"]
        assert "fresh" not in _projects(client, _supabase(OLD_OWNER))


# =============================================================================
# 2. WebSocket
# =============================================================================


def _ws_close_code(client: TestClient, token: str) -> int | None:
    try:
        with client.websocket_connect(f"/ws/jobs/{JOB}", subprotocols=["bearer", token]) as ws:
            ws.close()
            return None
    except WebSocketDisconnect as exc:
        return exc.code


class TestWebSocket:
    def test_linked_keycloak_identity_can_stream_its_job(self, client) -> None:
        assert _ws_close_code(client, _keycloak(KC_LINKED)) is None

    def test_supabase_identity_can_stream_its_job(self, client) -> None:
        assert _ws_close_code(client, _supabase(OLD_OWNER)) is None

    def test_unlinked_keycloak_identity_is_refused(self, client) -> None:
        assert _ws_close_code(client, _keycloak(KC_STRANGER)) == 4403


# =============================================================================
# 3. resolve_actor
# =============================================================================


class TestResolveActor:
    async def test_first_sight_issues_one_stable_actor(self, db_maker) -> None:
        async with db_maker() as db:
            first = await resolve_actor(db, _KC_ISS, "kc-new")
            assert first != "kc-new"
            assert await resolve_actor(db, _KC_ISS, "kc-new") == first

    async def test_same_subject_under_another_issuer_is_a_different_actor(
        self, db_maker
    ) -> None:
        async with db_maker() as db:
            other = await resolve_actor(db, "https://other.example/realms/x", KC_LINKED)
            assert other != OLD_OWNER

    async def test_losing_a_first_insert_race_returns_the_winner(self, db_maker) -> None:
        """Another request commits the same identity between our lookup and
        our insert: ours hits the PK, rolls back, and adopts the winner's
        actor instead of minting a second one."""
        async with db_maker() as db:
            real_commit = db.commit

            async def _commit_after_rival() -> None:
                async with db_maker() as rival:
                    rival.add(IdentityLink(issuer=_KC_ISS, subject="kc-race", actor_id="winner"))
                    await rival.commit()
                await real_commit()

            db.commit = _commit_after_rival  # type: ignore[method-assign]
            assert await resolve_actor(db, _KC_ISS, "kc-race") == "winner"

        async with db_maker() as db:
            rows = (
                await db.execute(select(IdentityLink).where(IdentityLink.subject == "kc-race"))
            ).scalars().all()
        assert [r.actor_id for r in rows] == ["winner"]


# =============================================================================
# 4. scripts/link_identity.py (fake DB-API connection)
# =============================================================================


class _FakeCursor:
    def __init__(self, owned: dict[str, int], link: str | None) -> None:
        self.owned = owned  # actor_id -> rows owned in each table
        self.link = link
        self.executed: list[str] = []
        self._next: tuple | None = None

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.executed.append(sql)
        if sql.startswith("SELECT count(*)"):
            self._next = (self.owned.get(params[0], 0),)
        elif sql.startswith("SELECT actor_id"):
            self._next = (self.link,) if self.link else None

    def fetchone(self):
        return self._next

    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _FakeConn:
    def __init__(self, cur: _FakeCursor) -> None:
        self.cur = cur

    def cursor(self) -> _FakeCursor:
        return self.cur

    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _run(cur: _FakeCursor, **kw) -> tuple[int, str]:
    out = io.StringIO()
    args = {"issuer": _KC_ISS, "subject": KC_LINKED, "actor_id": OLD_OWNER, "apply": False}
    code = link_identity.run(_FakeConn(cur), **{**args, **kw}, out=out)
    return code, out.getvalue()


def _wrote(cur: _FakeCursor) -> bool:
    return any(sql.startswith("INSERT") for sql in cur.executed)


class TestLinkScript:
    def test_dry_run_writes_nothing(self) -> None:
        cur = _FakeCursor({OLD_OWNER: 1}, link=None)
        code, out = _run(cur)
        assert code == 0 and not _wrote(cur)
        assert "dry-run" in out

    def test_apply_writes_the_link(self) -> None:
        cur = _FakeCursor({OLD_OWNER: 1}, link=None)
        code, out = _run(cur, apply=True)
        assert code == 0 and _wrote(cur)
        assert "applied" in out

    def test_relinking_an_empty_auto_actor_is_allowed(self) -> None:
        cur = _FakeCursor({OLD_OWNER: 1}, link="auto-actor")
        code, _ = _run(cur, apply=True)
        assert code == 0 and _wrote(cur)

    def test_refuses_to_orphan_data_without_force(self) -> None:
        cur = _FakeCursor({OLD_OWNER: 1, "auto-actor": 2}, link="auto-actor")
        code, out = _run(cur, apply=True)
        assert code == 1 and not _wrote(cur)
        assert "orphan" in out
        code, _ = _run(cur, apply=True, force=True)
        assert code == 0 and _wrote(cur)

    def test_already_linked_is_a_noop(self) -> None:
        cur = _FakeCursor({OLD_OWNER: 1}, link=OLD_OWNER)
        code, out = _run(cur, apply=True)
        assert code == 0 and not _wrote(cur)
        assert "already linked" in out

    def test_empty_or_oversized_args_are_rejected(self) -> None:
        assert link_identity.main(["--issuer", " ", "--subject", "s", "--actor-id", "a"]) == 2
        assert link_identity.main(
            ["--issuer", _KC_ISS, "--subject", "s", "--actor-id", "x" * 65]
        ) == 2
