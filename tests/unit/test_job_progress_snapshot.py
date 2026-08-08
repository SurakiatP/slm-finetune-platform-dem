"""Unit tests for the job-progress snapshot (ADR-008).

Covers the three pieces that together let a reloaded page paint immediately
instead of waiting on the next Pub/Sub frame:

  1. Writer  — ``workers.progress.publish_ws_message`` stores the serialized
     frame at ``job:{job_id}:last`` with a 24h TTL *before* publishing it.
  2. WS      — ``/ws/jobs/{job_id}`` sends that stored frame on connect,
     before any live frame.
  3. REST    — ``GET /api/v1/jobs/{job_id}/progress`` returns the same frame;
     404 when there is nothing (or nothing valid) to return, or when the
     `job_id` names no row at all; 403 when it names a row that exists but
     belongs to another user (ADR-012 — see ``TestRestSnapshotOwnership``
     below, which is the only class here that authenticates as a specific
     user; every other test in this file runs with ``user=None`` and so
     never touches the ownership branch at all).

Everything runs against fakeredis — no Docker, no real broker, no GPU.
``TestRestSnapshotOwnership`` additionally runs against an in-memory
aiosqlite DB, since ownership resolution (unlike the redis-only snapshot
lookup) has to walk real `Dataset`/`Project` rows.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from api.core.redis_client import JOB_SNAPSHOT_TTL_SECONDS, job_channel, job_snapshot_key
from api.schemas.progress import (
    EvaluationProgress,
    ExportProgress,
    JobCompleted,
    JobFailed,
    SDGProgress,
)
from workers.progress import publish_ws_message


def _sdg_frame(job_id: str = "job-1", generated: int = 80) -> SDGProgress:
    return SDGProgress(
        job_id=job_id,
        phase="generating",
        samples_generated=generated,
        samples_target=200,
    )


# =============================================================================
# 1. Writer — publish_ws_message
# =============================================================================


class TestSnapshotWrite:
    def test_key_format_is_channel_plus_last(self) -> None:
        """The snapshot key must be derivable from the channel name, not invented."""
        assert job_snapshot_key("abc") == "job:abc:last"
        assert job_snapshot_key("abc").startswith(job_channel("abc"))

    def test_publish_stores_snapshot_with_ttl(self, fake_redis_pubsub) -> None:
        client = fake_redis_pubsub.client
        frame = _sdg_frame()

        publish_ws_message(client, "job-1", frame)

        stored = client.get(job_snapshot_key("job-1"))
        assert stored is not None, "publish must leave a snapshot behind"
        assert frame.model_dump_json().encode() == stored

        ttl = client.ttl(job_snapshot_key("job-1"))
        assert 0 < ttl <= JOB_SNAPSHOT_TTL_SECONDS
        assert JOB_SNAPSHOT_TTL_SECONDS == 86_400, "ADR-008 fixes the snapshot TTL at 24h"

    def test_publish_still_happens(self, fake_redis_pubsub) -> None:
        """Snapshotting must not replace the live publish."""
        publish_ws_message(fake_redis_pubsub.client, "job-1", _sdg_frame())

        assert len(fake_redis_pubsub.published) == 1
        channel, payload = fake_redis_pubsub.published[0]
        assert channel == "job:job-1"
        assert b'"samples_generated":80' in payload

    def test_snapshot_written_before_publish(self, fake_redis_pubsub, monkeypatch) -> None:
        """ADR-008: SET precedes PUBLISH so a client subscribing in between
        reads a populated key rather than an empty one."""
        client = fake_redis_pubsub.client
        order: list[str] = []

        real_set = client.set
        real_publish = client.publish

        def spy_set(*a, **kw):
            order.append("set")
            return real_set(*a, **kw)

        def spy_publish(*a, **kw):
            order.append("publish")
            return real_publish(*a, **kw)

        monkeypatch.setattr(client, "set", spy_set)
        monkeypatch.setattr(client, "publish", spy_publish)

        publish_ws_message(client, "job-1", _sdg_frame())

        assert order == ["set", "publish"]

    @pytest.mark.parametrize(
        "frame",
        [
            JobCompleted(job_id="job-1", result={"ok": True}),
            JobFailed(job_id="job-1", error="boom"),
        ],
        ids=["completed", "failed"],
    )
    def test_terminal_frames_are_snapshotted_too(self, fake_redis_pubsub, frame) -> None:
        """Opening the page after a job finished must still show its outcome."""
        publish_ws_message(fake_redis_pubsub.client, "job-1", frame)

        stored = fake_redis_pubsub.client.get(job_snapshot_key("job-1"))
        assert stored is not None
        assert frame.type.value.encode() in stored

    def test_snapshot_failure_does_not_lose_the_live_frame(
        self, fake_redis_pubsub, monkeypatch
    ) -> None:
        """A degraded snapshot store must never cost a real-time frame."""
        client = fake_redis_pubsub.client

        def exploding_set(*a, **kw):
            raise RedisError("snapshot store down")

        monkeypatch.setattr(client, "set", exploding_set)

        publish_ws_message(client, "job-1", _sdg_frame())  # must not raise

        assert len(fake_redis_pubsub.published) == 1, "publish must survive a SET failure"

    def test_latest_frame_overwrites_previous(self, fake_redis_pubsub) -> None:
        client = fake_redis_pubsub.client
        publish_ws_message(client, "job-1", _sdg_frame(generated=10))
        publish_ws_message(client, "job-1", _sdg_frame(generated=90))

        stored = client.get(job_snapshot_key("job-1"))
        assert b'"samples_generated":90' in stored
        assert b'"samples_generated":10' not in stored

    def test_frames_are_isolated_per_job(self, fake_redis_pubsub) -> None:
        client = fake_redis_pubsub.client
        publish_ws_message(client, "job-a", _sdg_frame(job_id="job-a", generated=10))
        publish_ws_message(client, "job-b", _sdg_frame(job_id="job-b", generated=99))

        assert b'"samples_generated":10' in client.get(job_snapshot_key("job-a"))
        assert b'"samples_generated":99' in client.get(job_snapshot_key("job-b"))


# =============================================================================
# 2 + 3. Delivery — WebSocket on-connect and the REST endpoint
# =============================================================================


@pytest.fixture
def snapshot_store(monkeypatch):
    """Wire both delivery paths onto one in-memory Redis.

    The routers are async, but `TestClient` drives them from a worker thread —
    so seeding is done through a *sync* client sharing the same `FakeServer`
    rather than awaiting inside the test. That keeps every test below sync and
    avoids nesting an event loop inside TestClient's own.
    """
    import fakeredis
    import fakeredis.aioredis

    from api.routers import jobs as jobs_router
    from api.routers import websocket as ws_router

    server = fakeredis.FakeServer()
    seeder = fakeredis.FakeStrictRedis(server=server, decode_responses=True)

    def _factory() -> object:
        # A fresh async client per request, as the real factory does — but each
        # is backed by the same server, and `aclose()` must stay a no-op or the
        # shared server would be torn down mid-test.
        async_client = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)

        async def _aclose(*a, **kw) -> None:
            return None

        async_client.aclose = _aclose  # type: ignore[method-assign]
        return async_client

    monkeypatch.setattr(jobs_router, "get_redis_client", _factory)
    monkeypatch.setattr(ws_router, "get_redis_client", _factory)
    return seeder


@pytest.fixture
def client(snapshot_store) -> TestClient:
    from api.main import app

    return TestClient(app)


class TestRestSnapshotEndpoint:
    def test_returns_stored_frame(self, client, snapshot_store) -> None:
        frame = _sdg_frame(job_id="job-rest")
        snapshot_store.set(job_snapshot_key("job-rest"), frame.model_dump_json())

        resp = client.get("/api/v1/jobs/job-rest/progress")

        assert resp.status_code == 200
        body = resp.json()
        assert body["type"] == "sdg_progress"
        assert body["samples_generated"] == 80
        assert body["samples_target"] == 200
        assert body["job_id"] == "job-rest"

    @pytest.mark.parametrize(
        "frame",
        [
            ExportProgress(job_id="job-x", stage="quantizing", detail="Q4_K_M"),
            EvaluationProgress(job_id="job-x", phase="predicting", rows_done=5, rows_total=20),
        ],
        ids=["export", "evaluation"],
    )
    def test_new_frame_types_round_trip(self, client, snapshot_store, frame) -> None:
        """The two WSMessageType values added by ADR-008 must survive the union."""
        snapshot_store.set(job_snapshot_key("job-x"), frame.model_dump_json())

        resp = client.get("/api/v1/jobs/job-x/progress")

        assert resp.status_code == 200
        assert resp.json()["type"] == frame.type.value

    def test_404_when_no_frame_published_yet(self, client) -> None:
        """A queued job, or one whose TTL expired, is a 404 — not a 500, not a null."""
        resp = client.get("/api/v1/jobs/never-seen/progress")

        assert resp.status_code == 404
        assert "never-seen" in resp.text

    def test_corrupt_payload_is_404_not_500(self, client, snapshot_store) -> None:
        """A legacy/corrupt frame is equivalent to no frame; it must not 500."""
        snapshot_store.set(job_snapshot_key("job-bad"), '{"type":"not_a_real_type"}')

        resp = client.get("/api/v1/jobs/job-bad/progress")

        assert resp.status_code == 404

    def test_error_body_matches_the_standard_contract(self, client) -> None:
        """404s go through install_handlers, not a hand-rolled body."""
        resp = client.get("/api/v1/jobs/nope/progress")

        body = resp.json()
        assert isinstance(body, dict)
        assert body, "error body must not be empty"


# =============================================================================
# 3a. REST ownership split (ADR-012) — every other test in this file runs
#     anonymously (`user=None`), which skips `api/routers/jobs.py`'s
#     ownership branch entirely. These are the only tests that authenticate,
#     so they're the only ones that can exercise it.
# =============================================================================


class TestRestSnapshotOwnership:
    """`GET /api/v1/jobs/{job_id}/progress`, authenticated.

    Kept as its own DB-backed fixture rather than reusing `client`/
    `snapshot_store` directly: ownership resolution needs real
    `Project`/`Dataset` rows to join through (`resolve_job_owner`), which
    the redis-only fixtures above have no reason to carry.
    """

    @pytest.fixture
    def owned_job(self, snapshot_store, monkeypatch: pytest.MonkeyPatch):
        """A `TestClient` wired to both fakeredis (via `snapshot_store`,
        already monkeypatched onto `jobs_router.get_redis_client`) and an
        in-memory aiosqlite DB seeded with one Dataset (SDG job) owned by
        ALICE. Yields `(client, alice, bob, job_id)`; the caller picks the
        `state["user"]` and job id per test.
        """
        from sqlalchemy.dialects.postgresql import JSONB
        from sqlalchemy.ext.asyncio import (
            AsyncSession,
            async_sessionmaker,
            create_async_engine,
        )
        from sqlalchemy.ext.compiler import compiles

        from api.core.auth import CurrentUser, require_user
        from api.core.database import get_db
        from api.main import app
        from api.models.base import Base
        from api.models.dataset import Dataset
        from api.models.project import Project
        from api.schemas.enums import DatasetSource, JobStatus, TaskType

        @compiles(JSONB, "sqlite")
        def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
            return "JSON"

        alice = CurrentUser(id="alice-sub", email="alice@example.com")
        bob = CurrentUser(id="bob-sub", email="bob@example.com")
        job_id = "sdg-alice-owned"

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        state = {"user": alice}

        async def _setup() -> None:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with maker() as session:
                project = Project(
                    id=uuid4(), name="p", task_type=TaskType.QA, owner_id=alice.id
                )
                dataset = Dataset(
                    id=uuid4(),
                    project_id=project.id,
                    name="d",
                    task_type=TaskType.QA,
                    source=DatasetSource.SDG,
                    status=JobStatus.RUNNING,
                    celery_task_id=job_id,
                )
                session.add_all([project, dataset])
                await session.commit()

        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_setup())

        async def _get_db():
            async with maker() as session:
                yield session

        app.dependency_overrides[get_db] = _get_db
        app.dependency_overrides[require_user] = lambda: state["user"]
        with TestClient(app) as client:
            yield client, state, alice, bob, job_id
        app.dependency_overrides.clear()

    def test_owner_is_not_blocked_by_the_ownership_check(self, owned_job) -> None:
        client, state, alice, _bob, job_id = owned_job
        state["user"] = alice
        resp = client.get(f"/api/v1/jobs/{job_id}/progress")
        # No frame was published to redis for this job — ownership passes,
        # then it falls through to the ordinary "no frame yet" 404. Proves
        # the owner is NOT blocked by the ownership check (a 403 here would
        # be the bug this test exists to catch).
        assert resp.status_code == 404

    def test_non_owner_gets_403_not_404(self, owned_job) -> None:
        """THE pairing case for the REST job-progress endpoint (ADR-012): a
        job that exists but belongs to someone else is 403."""
        client, state, _alice, bob, job_id = owned_job
        state["user"] = bob
        resp = client.get(f"/api/v1/jobs/{job_id}/progress")
        assert resp.status_code == 403

    def test_unknown_job_id_still_gets_404(self, owned_job) -> None:
        """Pair for the test above: a `job_id` that resolves to no row at
        all (not just no redis frame) must stay 404, distinct from the 403
        an existing-but-not-yours job now gets. Catches a mutation that made
        the ownership branch 403 unconditionally, independent of
        `resolve_job_owner.found`."""
        client, state, _alice, bob, _job_id = owned_job
        state["user"] = bob
        resp = client.get(f"/api/v1/jobs/{uuid4()}/progress")
        assert resp.status_code == 404


class TestWebSocketSnapshotOnConnect:
    def test_sends_snapshot_immediately_on_connect(self, client, snapshot_store) -> None:
        """The whole point of ADR-008: connect mid-job, paint instantly."""
        frame = _sdg_frame(job_id="job-ws", generated=80)
        snapshot_store.set(job_snapshot_key("job-ws"), frame.model_dump_json())

        with client.websocket_connect("/ws/jobs/job-ws") as ws:
            received = ws.receive_json()
            # Close explicitly: the relay task blocks forever in `pubsub.listen()`,
            # and letting TestClient tear that down implicitly surfaces the
            # cancellation as a CancelledError out of the context manager.
            ws.close()

        assert received["type"] == "sdg_progress"
        assert received["samples_generated"] == 80, (
            "a client connecting after the job started must see the last known "
            "counter without waiting for the next publish"
        )

    def test_no_snapshot_means_no_frame_and_no_error(self, client) -> None:
        """Absent a snapshot the socket must still open cleanly and just wait."""
        with client.websocket_connect("/ws/jobs/job-empty") as ws:
            ws.close()
        # Reaching here without an exception is the assertion.
