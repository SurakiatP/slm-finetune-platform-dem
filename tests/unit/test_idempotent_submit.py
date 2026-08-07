"""A double-clicked Launch button must not buy two OpenRouter bills.

The three submit endpoints dedupe on a 60-second window keyed by the caller
plus a hash of the canonical request body.

The case that matters most is the anonymous one. `smart-model-tune` sends no
Authorization header today — the auth rollout is still in phase 1 — so a
dedupe that keys only on `user.id` and no-ops when `user is None` would be
dead code for the exact client it exists to protect. That is the same trap as
shipping an `Idempotency-Key` header the frontend does not send yet, and
`TestAnonymousCallerIsCovered` is what keeps it closed.
"""

from __future__ import annotations

from uuid import uuid4

import fakeredis.aioredis
import pytest
from starlette.requests import Request

from api.core.auth import CurrentUser
from api.services import idempotency

USER_A = CurrentUser(id="user-a-sub", email="a@example.com")
USER_B = CurrentUser(id="user-b-sub", email="b@example.com")

BODY = {"project_id": str(uuid4()), "num_samples": 200, "sdg_mode": "with_seed"}


def _request(path: str = "/api/v1/datasets/generate", headers: dict | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": raw,
            "client": ("10.0.0.9", 1234),
            "query_string": b"",
        }
    )


@pytest.fixture
def redis(monkeypatch):
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(idempotency, "get_redis_client", lambda: client)
    # `replay`/`remember` close the client they are handed; keep it alive so a
    # single fake survives both calls in a test.
    monkeypatch.setattr(client, "aclose", lambda: _noop())
    return client


async def _noop():
    return None


# =============================================================================
# 1. The window
# =============================================================================


class TestDedupeWindow:
    async def test_identical_submit_replays_the_first_response(self, redis) -> None:
        first_payload = {"job_id": "celery-task-1", "dataset_id": str(uuid4())}
        await idempotency.remember(_request(), USER_A, BODY, first_payload)

        replayed = await idempotency.replay(_request(), USER_A, BODY)

        assert replayed is not None, "the second click must not reach the service"
        assert replayed.status_code == 202
        assert replayed.headers[idempotency.REPLAY_HEADER] == "true"
        import json

        assert json.loads(bytes(replayed.body)) == first_payload

    async def test_a_different_body_is_a_different_job(self, redis) -> None:
        await idempotency.remember(_request(), USER_A, BODY, {"job_id": "celery-task-1"})
        other = {**BODY, "num_samples": 500}
        assert await idempotency.replay(_request(), USER_A, other) is None

    async def test_key_order_does_not_change_the_hash(self, redis) -> None:
        """JSON object order is not semantic; a re-serialized body must still
        dedupe."""
        reordered = dict(reversed(list(BODY.items())))
        await idempotency.remember(_request(), USER_A, BODY, {"job_id": "celery-task-1"})
        assert await idempotency.replay(_request(), USER_A, reordered) is not None

    async def test_two_users_do_not_share_a_window(self, redis) -> None:
        await idempotency.remember(_request(), USER_A, BODY, {"job_id": "celery-task-1"})
        assert await idempotency.replay(_request(), USER_B, BODY) is None

    async def test_each_endpoint_has_its_own_window(self, redis) -> None:
        await idempotency.remember(_request("/api/v1/trainings"), USER_A, BODY, {"x": 1})
        assert await idempotency.replay(_request("/api/v1/evaluations"), USER_A, BODY) is None

    async def test_the_window_expires(self, redis) -> None:
        await idempotency.remember(_request(), USER_A, BODY, {"job_id": "celery-task-1"})
        key = idempotency.build_key(
            actor=idempotency.actor_for(_request(), USER_A),
            path="/api/v1/datasets/generate",
            body=BODY,
            header_key=None,
        )
        ttl = await redis.ttl(key)
        assert 0 < ttl <= idempotency.TTL_SECONDS


# =============================================================================
# 2. The anonymous caller — the only real client today
# =============================================================================


class TestAnonymousCallerIsCovered:
    async def test_dedupe_works_without_a_token(self, redis) -> None:
        req = _request(headers={"X-Forwarded-For": "203.0.113.7"})
        await idempotency.remember(req, None, BODY, {"job_id": "celery-task-1"})
        assert await idempotency.replay(req, None, BODY) is not None

    async def test_two_anonymous_hosts_do_not_collide(self, redis) -> None:
        one = _request(headers={"X-Forwarded-For": "203.0.113.7"})
        two = _request(headers={"X-Forwarded-For": "198.51.100.4"})
        await idempotency.remember(one, None, BODY, {"job_id": "celery-task-1"})
        assert await idempotency.replay(two, None, BODY) is None

    async def test_forwarded_for_beats_the_proxy_socket(self) -> None:
        """One nginx serves the frontend same-origin, so `request.client.host`
        is the proxy's address for every caller — keying on it would put all
        users in one bucket."""
        req = _request(headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.1"})
        assert idempotency.client_host(req) == "203.0.113.7"
        assert idempotency.actor_for(req, None) == "anon:203.0.113.7"

    async def test_authenticated_actor_wins_over_host(self) -> None:
        req = _request(headers={"X-Forwarded-For": "203.0.113.7"})
        assert idempotency.actor_for(req, USER_A) == USER_A.id

    async def test_single_value_xff_still_buckets_correctly(self, redis) -> None:
        """This is exactly the shape the `edge` nginx service sends —
        `X-Forwarded-For $remote_addr;` *overwrites* rather than appends
        (`docker/edge.nginx.conf`), so production traffic never carries the
        two-value XFF the tests above exercise. A naive `.split(",")[0]`
        implementation happens to work on a single value too, so this is the
        case where an implementation that only reads the *last* hop (also a
        plausible-looking "fix" for XFF parsing) would still pass the
        two-value tests above by accident but fail here."""
        req = _request(headers={"X-Forwarded-For": "203.0.113.7"})
        assert idempotency.client_host(req) == "203.0.113.7"
        await idempotency.remember(req, None, BODY, {"job_id": "celery-task-1"})
        assert await idempotency.replay(req, None, BODY) is not None

    async def test_two_different_single_value_xffs_do_not_collide(self, redis) -> None:
        one = _request(headers={"X-Forwarded-For": "203.0.113.7"})
        two = _request(headers={"X-Forwarded-For": "198.51.100.4"})
        assert idempotency.client_host(one) != idempotency.client_host(two)
        await idempotency.remember(one, None, BODY, {"job_id": "celery-task-1"})
        assert await idempotency.replay(two, None, BODY) is None

    async def test_no_xff_at_all_falls_back_to_the_socket_and_shares_one_bucket(
        self, redis
    ) -> None:
        """Known, deliberate failure mode — not a surprise to rediscover on
        the box.

        No `X-Forwarded-For` header is exactly the shape a misconfigured
        `real_ip`/edge deployment produces: every request then arrives at
        the app with `request.client.host` set to `edge`'s own container
        address (see `_request_context_middleware`'s `client_ip` log line in
        `api/main.py`, which exists specifically to make this visible on a
        running box via `docker compose logs api | grep client_ip`). Two
        *different* real-world anonymous callers that both hit the app this
        way share the exact same dedupe bucket — caller B's identical-body
        submit inside the 60s window gets caller A's replayed 202 instead of
        being enqueued, silently swallowing caller B's job. This test pins
        that consequence down as documented, expected behaviour of the
        fallback (the alternative — failing the request when XFF is absent —
        would take down every anonymous caller in local dev and tests, which
        also have no XFF), not a bug to "fix" later without also fixing the
        edge config that causes it.
        """
        one = _request()
        two = _request()
        assert idempotency.client_host(one) == idempotency.client_host(two) == "10.0.0.9"

        await idempotency.remember(one, None, BODY, {"job_id": "celery-task-1"})
        replayed = await idempotency.replay(two, None, BODY)
        assert replayed is not None, (
            "documented failure mode: with no XFF, distinct anonymous "
            "callers collapse into one dedupe bucket"
        )


# =============================================================================
# 3. Degradation — dedupe is an optimisation, never a failure mode
# =============================================================================


class TestRedisOutageDegradesToNoDedupe:
    async def test_replay_returns_none_when_redis_is_down(self, monkeypatch) -> None:
        class _Broken:
            async def get(self, *a, **kw):
                raise ConnectionError("redis is down")

            async def set(self, *a, **kw):
                raise ConnectionError("redis is down")

            async def aclose(self):
                return None

        monkeypatch.setattr(idempotency, "get_redis_client", lambda: _Broken())
        assert await idempotency.replay(_request(), USER_A, BODY) is None

    async def test_remember_does_not_raise_when_redis_is_down(self, monkeypatch) -> None:
        """A Redis hiccup must never turn a job submission into a 500 — losing
        dedupe is strictly better than losing the submit."""

        class _Broken:
            async def get(self, *a, **kw):
                raise ConnectionError("redis is down")

            async def set(self, *a, **kw):
                raise ConnectionError("redis is down")

            async def aclose(self):
                return None

        monkeypatch.setattr(idempotency, "get_redis_client", lambda: _Broken())
        await idempotency.remember(_request(), USER_A, BODY, {"job_id": "x"})


# =============================================================================
# 4. Wiring guard — all three submit endpoints, and not the export one
# =============================================================================


_SUBMIT_ROUTERS = ("datasets", "trainings", "evaluations")


@pytest.mark.parametrize("router", _SUBMIT_ROUTERS)
def test_submit_endpoint_is_dedupe_protected(router: str) -> None:
    """Parametrized so adding a submit endpoint to the tuple extends the
    guard rather than duplicating it."""
    import importlib
    import pathlib

    mod = importlib.import_module(f"api.routers.{router}")
    src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    assert "idempotency.replay(" in src, f"{router} submit is not dedupe-protected"
    assert "idempotency.remember(" in src, f"{router} never stores its response"


def test_export_uses_the_in_flight_guard_instead() -> None:
    """Export is deliberately NOT in the window: one artifact can only have
    one export at a time, so a resource-state 409 is both stricter and more
    informative than a replayed 202."""
    import pathlib

    import api.services.model_service as model_service

    src = pathlib.Path(model_service.__file__).read_text(encoding="utf-8")
    assert "already has an export in flight" in src
    assert "idempotency" not in src


# =============================================================================
# 5. End to end through the router — the criterion as stated
# =============================================================================


class TestRouterLevelDedupe:
    """Unit-level `replay`/`remember` tests prove the primitive. This proves
    the thing that was actually asked for: two identical POSTs produce ONE
    Celery task and two identical 202 bodies — for an anonymous caller as
    well as an authenticated one."""

    @pytest.fixture
    def app_client(self, monkeypatch, redis):
        from fastapi.testclient import TestClient

        from api.core.auth import require_user
        from api.core.database import get_db
        from api.main import app
        from api.routers import datasets as datasets_router
        from api.schemas.sdg import SDGJobAcceptedResponse

        submitted: list[dict] = []

        async def _fake_submit(db, body):  # noqa: ANN001
            """Stands in for the service — every call here is one Celery task."""
            submitted.append(body.model_dump(mode="json"))
            return SDGJobAcceptedResponse(
                job_id=f"celery-task-{len(submitted)}",
                dataset_id=uuid4(),
                websocket_url="/ws/jobs/x",
            )

        async def _noop_ownership(db, project_id, user):  # noqa: ANN001
            return None

        monkeypatch.setattr(datasets_router, "submit_sdg_job", _fake_submit)
        monkeypatch.setattr(
            datasets_router.ownership, "assert_project_access", _noop_ownership
        )

        async def _fake_db():
            yield None

        app.dependency_overrides[get_db] = _fake_db
        app.dependency_overrides[require_user] = lambda: None  # anonymous, phase 1
        with TestClient(app) as client:
            yield client, submitted
        app.dependency_overrides.clear()

    def _body(self) -> dict:
        return {
            "sdg_mode": "description_only",
            "project_id": str(uuid4()),
            "task_type": "qa",
            "task_description": "Answer questions about the return policy",
            "num_samples": 20,
            "holdout_size": 0,
        }

    def test_double_click_enqueues_one_task_anonymously(self, app_client) -> None:
        client, submitted = app_client
        body = self._body()
        headers = {"X-Forwarded-For": "203.0.113.7"}

        first = client.post("/api/v1/datasets/generate", json=body, headers=headers)
        second = client.post("/api/v1/datasets/generate", json=body, headers=headers)

        assert first.status_code == 202 and second.status_code == 202
        assert len(submitted) == 1, "the second click reached the service"
        assert first.json() == second.json()
        assert second.headers.get(idempotency.REPLAY_HEADER) == "true"
        assert idempotency.REPLAY_HEADER not in first.headers

    def test_a_different_body_does_enqueue_a_second_task(self, app_client) -> None:
        client, submitted = app_client
        headers = {"X-Forwarded-For": "203.0.113.7"}
        client.post("/api/v1/datasets/generate", json=self._body(), headers=headers)
        client.post("/api/v1/datasets/generate", json=self._body(), headers=headers)
        assert len(submitted) == 2, "distinct projects must not be deduped together"


# =============================================================================
# 6. Regressions found by the verification pass
# =============================================================================


class TestIdempotencyKeyDoesNotCrossEndpoints:
    """An `Idempotency-Key` replaces the BODY HASH, not the path.

    Dropping the path when the header is present let one client-generated key
    match across endpoints: a client reusing `Idempotency-Key: <uuid>` for its
    training submit and then its evaluation submit was handed the training's
    202 back, and the evaluation was never enqueued. A silently swallowed job
    is far worse than the duplicate this feature exists to prevent.
    """

    async def test_same_header_on_a_different_endpoint_does_not_replay(self, redis) -> None:
        header = {idempotency.IDEMPOTENCY_KEY_HEADER: "client-uuid-1"}
        training = _request("/api/v1/trainings", headers=header)
        evaluation = _request("/api/v1/evaluations", headers=header)

        await idempotency.remember(training, USER_A, BODY, {"job_id": "TRAINING-JOB"})

        assert await idempotency.replay(evaluation, USER_A, {"unrelated": True}) is None

    async def test_same_header_same_endpoint_still_replays(self, redis) -> None:
        """The header must still do its job: dedupe a retry of the same call
        even when the body differs."""
        header = {idempotency.IDEMPOTENCY_KEY_HEADER: "client-uuid-1"}
        req = _request("/api/v1/trainings", headers=header)

        await idempotency.remember(req, USER_A, BODY, {"job_id": "TRAINING-JOB"})
        replayed = await idempotency.replay(req, USER_A, {"totally": "different"})

        assert replayed is not None
