"""WebSocket authorization for `/ws/jobs/{job_id}` (ADR-009).

Before this, the endpoint's first statement was `await ws.accept()` — anyone
holding (or guessing) a Celery task id could stream anyone's job.
`BACKEND_GAP_ANALYSIS.md` names that anti-pattern directly: the UUID must stop
standing in for an authorization check.

Three properties are load-bearing and each is easy to get subtly wrong:

1. **The subprotocol must be echoed on accept.** Browsers close the connection
   immediately if the server doesn't select one of the offered protocols, so
   forgetting the echo breaks every real client while every server-side test
   that ignores it still passes.
2. **Unknown job and someone-else's job must be indistinguishable.** Otherwise
   the endpoint is an oracle for enumerating job ids.
3. **Nothing offered ≠ something malformed.** Only the former is anonymous.
   Folding them together would let a phase-2 client bypass auth by offering
   deliberate garbage.

In-memory aiosqlite + fakeredis; no Postgres, no network.
"""

from __future__ import annotations

import time
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from starlette.websockets import WebSocketDisconnect

from api.core import auth as auth_mod
from api.core.config import get_settings
from api.core.database import get_db
from api.core.redis_client import job_snapshot_key
from api.main import app
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.project import Project
from api.schemas.enums import DatasetSource, JobStatus, TaskType
from api.schemas.progress import SDGProgress


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


_SECRET = "ws-test-hs256-secret-padded-past-32-bytes"
_AUD = "authenticated"
ALICE_SUB = "alice-sub"
BOB_SUB = "bob-sub"

ALICE_JOB = "job-owned-by-alice"
ORPHAN_JOB = "job-with-no-owner"
UNKNOWN_JOB = "job-that-does-not-exist"

_CLOSE_UNAUTHENTICATED = 4401
_CLOSE_FORBIDDEN = 4403


def _token(sub: str, **overrides) -> str:
    claims = {
        "sub": sub,
        "aud": _AUD,
        "exp": int(time.time()) + 3600,
        **overrides,
    }
    return jwt.encode(claims, _SECRET, algorithm="HS256")


@pytest.fixture
def auth_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", _SECRET)
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", _AUD)
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    get_settings.cache_clear()
    auth_mod._reset_jwks_client_cache()
    yield monkeypatch
    get_settings.cache_clear()
    auth_mod._reset_jwks_client_cache()


@pytest.fixture
def phase_two(auth_env):
    auth_env.setenv("AUTH_REQUIRED", "true")
    get_settings.cache_clear()
    return auth_env


@pytest.fixture
async def seeded_db():
    """One job owned by alice, one owned by nobody (pre-auth row)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as session:
        for owner, job in ((ALICE_SUB, ALICE_JOB), (None, ORPHAN_JOB)):
            project = Project(
                id=uuid4(), name=f"p-{job}", task_type=TaskType.QA, owner_id=owner
            )
            session.add(project)
            session.add(
                Dataset(
                    id=uuid4(), project_id=project.id, name=f"d-{job}",
                    source=DatasetSource.SDG, task_type=TaskType.QA,
                    status=JobStatus.RUNNING, celery_task_id=job,
                )
            )
        await session.commit()

    yield maker
    await engine.dispose()


@pytest.fixture
def client(seeded_db, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """App wired to the in-memory DB and a fake Redis holding one snapshot."""
    import fakeredis
    import fakeredis.aioredis

    from api.routers import websocket as ws_router

    server = fakeredis.FakeServer()
    seeder = fakeredis.FakeStrictRedis(server=server, decode_responses=True)
    frame = SDGProgress(job_id=ALICE_JOB, phase="generating", samples_target=200)
    seeder.set(job_snapshot_key(ALICE_JOB), frame.model_dump_json())

    def _redis_factory():
        c = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)

        async def _aclose(*a, **kw) -> None:
            return None

        c.aclose = _aclose  # type: ignore[method-assign]
        return c

    monkeypatch.setattr(ws_router, "get_redis_client", _redis_factory)

    async def _override_db():
        async with seeded_db() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _connect(client: TestClient, job: str, subprotocols=None):
    return client.websocket_connect(f"/ws/jobs/{job}", subprotocols=subprotocols)


def _expect_close(client: TestClient, job: str, subprotocols, code: int) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with _connect(client, job, subprotocols):
            pass
    assert exc.value.code == code, f"expected close {code}, got {exc.value.code}"


# =============================================================================
# 1. Phase 1 — the compatibility guarantee
# =============================================================================


class TestPhaseOne:
    def test_anonymous_connect_still_works(self, client, auth_env) -> None:
        """No subprotocol offered + AUTH_REQUIRED=false must behave exactly as
        it did before auth existed — this is what keeps the current frontend,
        which sends no credential, working after deploy."""
        with _connect(client, ALICE_JOB) as ws:
            frame = ws.receive_json()
            ws.close()
        assert frame["job_id"] == ALICE_JOB

    def test_anonymous_reaches_a_job_it_does_not_own(self, client, auth_env) -> None:
        """Phase 1 is a compatibility window, not a security state — say so
        plainly in a test rather than discovering it in production."""
        with _connect(client, ORPHAN_JOB) as ws:
            ws.close()

    def test_invalid_token_is_rejected_even_in_phase_one(self, client, auth_env) -> None:
        """Tolerating *absence* must not mean tolerating garbage, or the whole
        compatibility window would accept forged credentials."""
        _expect_close(
            client, ALICE_JOB, ["bearer", "not-a-real-jwt"], _CLOSE_UNAUTHENTICATED
        )


# =============================================================================
# 2. Phase 2 — enforced
# =============================================================================


class TestPhaseTwo:
    def test_anonymous_is_refused(self, client, phase_two) -> None:
        _expect_close(client, ALICE_JOB, None, _CLOSE_UNAUTHENTICATED)

    def test_owner_is_accepted_and_gets_the_snapshot_first(
        self, client, phase_two
    ) -> None:
        """ADR-008's snapshot-on-connect must survive the authorization gate."""
        with _connect(client, ALICE_JOB, ["bearer", _token(ALICE_SUB)]) as ws:
            frame = ws.receive_json()
            ws.close()
        assert frame["type"] == "sdg_progress"
        assert frame["job_id"] == ALICE_JOB

    def test_selected_subprotocol_is_echoed_back(self, client, phase_two) -> None:
        """Without this the browser closes the connection on its own, and every
        real client breaks while server-side tests stay green."""
        with _connect(client, ALICE_JOB, ["bearer", _token(ALICE_SUB)]) as ws:
            assert ws.accepted_subprotocol == "bearer"
            ws.close()

    def test_expired_token_is_refused(self, client, phase_two) -> None:
        stale = _token(ALICE_SUB, exp=int(time.time()) - 10)
        _expect_close(client, ALICE_JOB, ["bearer", stale], _CLOSE_UNAUTHENTICATED)


# =============================================================================
# 3. Ownership — and the oracle it must not become
# =============================================================================


class TestJobOwnership:
    def test_other_users_job_is_refused(self, client, phase_two) -> None:
        _expect_close(
            client, ALICE_JOB, ["bearer", _token(BOB_SUB)], _CLOSE_FORBIDDEN
        )

    def test_unknown_job_and_forbidden_job_are_indistinguishable(
        self, client, phase_two
    ) -> None:
        """If these differed, a caller could enumerate which job ids exist."""
        codes = []
        for job in (ALICE_JOB, UNKNOWN_JOB):
            with pytest.raises(WebSocketDisconnect) as exc:
                with _connect(client, job, ["bearer", _token(BOB_SUB)]):
                    pass
            codes.append(exc.value.code)
        assert codes[0] == codes[1] == _CLOSE_FORBIDDEN

    def test_null_owner_job_is_refused(self, client, phase_two) -> None:
        """A pre-auth row is owned by nobody, not by everybody."""
        _expect_close(
            client, ORPHAN_JOB, ["bearer", _token(ALICE_SUB)], _CLOSE_FORBIDDEN
        )


# =============================================================================
# 4. Subprotocol shape — absent vs malformed
# =============================================================================


class TestSubprotocolShape:
    @pytest.mark.parametrize(
        "subprotocols",
        [["bearer"], ["bearer", "a", "b"], ["notbearer", "tok"], ["justonevalue"]],
        ids=["scheme-only", "too-many", "wrong-scheme", "single-junk"],
    )
    def test_malformed_offer_is_refused_in_phase_one(
        self, client, auth_env, subprotocols
    ) -> None:
        """Offering *something* that isn't a credential is a caller error, not
        anonymity. Treating it as absent would hand phase-2 clients a bypass:
        offer junk, get in as anonymous."""
        _expect_close(client, ALICE_JOB, subprotocols, _CLOSE_UNAUTHENTICATED)

    def test_scheme_is_case_insensitive(self, client, phase_two) -> None:
        with _connect(client, ALICE_JOB, ["Bearer", _token(ALICE_SUB)]) as ws:
            ws.close()
