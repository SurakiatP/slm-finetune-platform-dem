"""Unit tests for the job-submit idempotency dedupe window (T3).

Everything runs against fakeredis's async client, monkeypatched over
`get_redis_client` as imported into `api.services.idempotency` — no Docker,
no real broker.
"""

from __future__ import annotations

import json

import fakeredis
import pytest
from redis.exceptions import RedisError
from starlette.requests import Request

from api.core.auth import CurrentUser
from api.services import idempotency
from api.services.idempotency import (
    IDEMPOTENCY_KEY_HEADER,
    REPLAY_HEADER,
    TTL_SECONDS,
    actor_for,
    build_key,
    canonical_json,
    client_host,
    remember,
    replay,
)


def _make_request(
    headers: dict[str, str] | None = None,
    client: tuple[str, int] | None = ("1.2.3.4", 12345),
    path: str = "/api/v1/datasets/generate",
    method: str = "POST",
) -> Request:
    """Build a minimal starlette Request over a bare ASGI scope."""
    encoded_headers = [
        (k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()
    ]
    scope: dict = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": encoded_headers,
        "query_string": b"",
    }
    if client is not None:
        scope["client"] = client
    return Request(scope)


class _ExplodingRedis:
    """Stands in for `get_redis_client()` when Redis itself is unreachable."""

    async def get(self, key: str) -> None:
        raise RedisError("connection refused")

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        raise RedisError("connection refused")

    async def aclose(self) -> None:
        return None


@pytest.fixture
def fake_redis(monkeypatch):
    """A real (in-memory) async Redis client backing the module under test."""
    server = fakeredis.FakeServer()

    def _factory():
        return fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)

    monkeypatch.setattr(idempotency, "get_redis_client", _factory)
    return server


@pytest.fixture
def exploding_redis(monkeypatch):
    """Simulate a Redis outage: every call raises `RedisError`."""
    monkeypatch.setattr(idempotency, "get_redis_client", lambda: _ExplodingRedis())


# =============================================================================
# canonical_json / build_key / actor_for / client_host — pure functions
# =============================================================================


class TestKeyDerivation:
    def test_same_body_and_actor_produce_same_key(self) -> None:
        body = {"model": "qwen", "task_type": "qa"}
        k1 = build_key(actor="user-1", path="/api/v1/x", body=body, header_key=None)
        k2 = build_key(actor="user-1", path="/api/v1/x", body=body, header_key=None)
        assert k1 == k2

    def test_key_is_order_independent(self) -> None:
        body_a = {"a": 1, "b": 2}
        body_b = {"b": 2, "a": 1}
        assert canonical_json(body_a) == canonical_json(body_b)

        k1 = build_key(actor="user-1", path="/api/v1/x", body=body_a, header_key=None)
        k2 = build_key(actor="user-1", path="/api/v1/x", body=body_b, header_key=None)
        assert k1 == k2

    def test_authenticated_and_anonymous_actors_differ(self) -> None:
        request = _make_request(client=("9.9.9.9", 1))
        user = CurrentUser(id="user-42", email="a@example.com")

        auth_actor = actor_for(request, user)
        anon_actor = actor_for(request, None)

        assert auth_actor != anon_actor
        assert auth_actor == "user-42"
        assert anon_actor == "anon:9.9.9.9"

        body = {"same": "body"}
        k_auth = build_key(actor=auth_actor, path="/api/v1/x", body=body, header_key=None)
        k_anon = build_key(actor=anon_actor, path="/api/v1/x", body=body, header_key=None)
        assert k_auth != k_anon

    def test_anonymous_actor_does_not_require_a_user(self) -> None:
        """`actor_for` must produce a real bucket for `user is None` — this is
        the exact caller (`smart-model-tune`, no Authorization header) the
        feature exists to protect, so there must be no early-return that
        skips dedupe for anonymous callers."""
        request = _make_request(client=("5.5.5.5", 1))
        actor = actor_for(request, None)
        assert actor == "anon:5.5.5.5"

    def test_x_forwarded_for_first_hop_wins_over_socket_address(self) -> None:
        request = _make_request(
            headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.1"},
            client=("10.0.0.1", 12345),
        )
        assert client_host(request) == "203.0.113.7"

    def test_no_forwarded_header_falls_back_to_socket_address(self) -> None:
        request = _make_request(client=("10.0.0.1", 12345))
        assert client_host(request) == "10.0.0.1"

    def test_explicit_idempotency_key_overrides_body_hash(self) -> None:
        body_1 = {"x": 1}
        body_2 = {"x": 2}  # different body

        k1 = build_key(actor="user-1", path="/api/v1/x", body=body_1, header_key="same-key")
        k2 = build_key(actor="user-1", path="/api/v1/x", body=body_2, header_key="same-key")
        assert k1 == k2, "an explicit Idempotency-Key must win over the body hash"

        k3 = build_key(actor="user-1", path="/api/v1/x", body=body_1, header_key=None)
        assert k3 != k1, "omitting the header must fall back to the body hash"


# =============================================================================
# replay() / remember() — the Redis-backed round trip
# =============================================================================


class TestReplayAndRemember:
    @pytest.mark.asyncio
    async def test_remember_then_replay_returns_stored_body(self, fake_redis) -> None:
        request = _make_request()
        payload = {"job_id": "job-123", "status": "queued"}

        await remember(request, None, {"a": 1}, payload)
        result = await replay(request, None, {"a": 1})

        assert result is not None
        assert result.status_code == 202
        assert json.loads(result.body) == payload
        assert result.headers[REPLAY_HEADER] == "true"

    @pytest.mark.asyncio
    async def test_replay_miss_returns_none(self, fake_redis) -> None:
        request = _make_request()
        result = await replay(request, None, {"never": "stored"})
        assert result is None

    @pytest.mark.asyncio
    async def test_replay_respects_actor_and_body_key_derivation(self, fake_redis) -> None:
        """A different actor making the same-shaped request must not see a replay."""
        request_a = _make_request(client=("1.1.1.1", 1))
        request_b = _make_request(client=("2.2.2.2", 1))
        body = {"same": "body"}

        await remember(request_a, None, body, {"result": "for-a"})

        assert await replay(request_a, None, body) is not None
        assert await replay(request_b, None, body) is None

    @pytest.mark.asyncio
    async def test_idempotency_key_header_makes_replay_body_irrelevant(self, fake_redis) -> None:
        request_first = _make_request(headers={IDEMPOTENCY_KEY_HEADER: "abc-123"})
        request_second = _make_request(headers={IDEMPOTENCY_KEY_HEADER: "abc-123"})

        await remember(request_first, None, {"x": 1}, {"job_id": "job-1"})
        result = await replay(request_second, None, {"x": "totally different"})

        assert result is not None
        assert json.loads(result.body) == {"job_id": "job-1"}


# =============================================================================
# Redis outage — must degrade to "no dedupe", never raise / 500
# =============================================================================


class TestRedisOutage:
    @pytest.mark.asyncio
    async def test_replay_returns_none_on_redis_outage(self, exploding_redis) -> None:
        request = _make_request()
        result = await replay(request, None, {"a": 1})
        assert result is None

    @pytest.mark.asyncio
    async def test_remember_does_not_raise_on_redis_outage(self, exploding_redis) -> None:
        request = _make_request()
        # Must not raise.
        await remember(request, None, {"a": 1}, {"job_id": "job-1"})


def test_ttl_is_sixty_seconds() -> None:
    assert TTL_SECONDS == 60
