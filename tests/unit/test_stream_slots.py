"""Unit tests for the concurrent-SSE-stream limiter (T1).

Everything runs against fakeredis's async client, monkeypatched over
`get_redis_client` as imported into `api.services.stream_slots` — no
Docker, no real broker. Follows the same fixture pattern as
`test_idempotency.py`.
"""

from __future__ import annotations

import time

import fakeredis
import pytest
from fastapi import HTTPException
from redis.exceptions import RedisError

from api.services import stream_slots
from api.services.stream_slots import acquire, release


class _ExplodingRedis:
    """Stands in for `get_redis_client()` when Redis itself is unreachable."""

    def pipeline(self, transaction: bool = True):
        raise RedisError("connection refused")

    async def aclose(self) -> None:
        return None


@pytest.fixture
def fake_redis(monkeypatch):
    """A real (in-memory) async Redis client backing the module under test."""
    server = fakeredis.FakeServer()

    def _factory():
        return fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)

    monkeypatch.setattr(stream_slots, "get_redis_client", _factory)
    return server


@pytest.fixture
def exploding_redis(monkeypatch):
    """Simulate a Redis outage: every call raises `RedisError`."""
    monkeypatch.setattr(stream_slots, "get_redis_client", lambda: _ExplodingRedis())


@pytest.fixture(autouse=True)
def _tight_caps(monkeypatch):
    """Small caps so tests don't need dozens of acquires to trip a limit."""
    settings = stream_slots.get_settings()
    monkeypatch.setattr(settings, "inference_stream_max_per_actor", 2)
    monkeypatch.setattr(settings, "inference_stream_max_global", 3)
    monkeypatch.setattr(settings, "inference_stream_max_seconds", 300.0)
    monkeypatch.setattr(settings, "inference_stream_idle_timeout_seconds", 60.0)
    monkeypatch.setattr(settings, "quota_retry_after_seconds", 30)
    return settings


# =============================================================================
# Per-actor cap
# =============================================================================


class TestPerActorCap:
    @pytest.mark.asyncio
    async def test_third_acquire_for_same_actor_is_rejected(self, fake_redis) -> None:
        s1 = await acquire("user-1")
        s2 = await acquire("user-1")
        assert s1 is not None
        assert s2 is not None

        with pytest.raises(HTTPException) as exc_info:
            await acquire("user-1")
        assert exc_info.value.status_code == 429
        assert "per caller" in exc_info.value.detail
        assert exc_info.value.headers["Retry-After"] == "30"

    @pytest.mark.asyncio
    async def test_rejected_acquire_leaves_no_member(self, fake_redis) -> None:
        await acquire("user-2")
        await acquire("user-2")
        with pytest.raises(HTTPException):
            await acquire("user-2")

        redis = fakeredis.aioredis.FakeRedis(server=fake_redis, decode_responses=True)
        assert await redis.zcard(stream_slots._actor_key("user-2")) == 2
        await redis.aclose()

    @pytest.mark.asyncio
    async def test_release_frees_a_slot(self, fake_redis) -> None:
        s1 = await acquire("user-3")
        await acquire("user-3")
        await release("user-3", s1)
        # A slot was freed, so a third acquire now succeeds.
        s3 = await acquire("user-3")
        assert s3 is not None

    @pytest.mark.asyncio
    async def test_different_actors_have_independent_buckets(self, fake_redis) -> None:
        await acquire("user-a")
        await acquire("user-a")
        # user-b's own per-actor bucket is unaffected by user-a's.
        assert await acquire("user-b") is not None


# =============================================================================
# Global cap
# =============================================================================


class TestGlobalCap:
    @pytest.mark.asyncio
    async def test_global_cap_enforced_across_distinct_actors(self, fake_redis) -> None:
        await acquire("actor-1")
        await acquire("actor-2")
        await acquire("actor-3")  # global count now 3 == cap

        with pytest.raises(HTTPException) as exc_info:
            await acquire("actor-4")
        assert exc_info.value.status_code == 429
        assert "global limit" in exc_info.value.detail
        assert exc_info.value.headers["Retry-After"] == "30"


# =============================================================================
# Expiry-based self-healing
# =============================================================================


class TestExpiredSlotsSelfHeal:
    @pytest.mark.asyncio
    async def test_expired_slot_is_purged_on_next_acquire(self, fake_redis) -> None:
        redis = fakeredis.aioredis.FakeRedis(server=fake_redis, decode_responses=True)
        # Plant a slot for user-4 that already expired.
        await redis.zadd(stream_slots._actor_key("user-4"), {"stale": time.time() - 5})
        await redis.aclose()

        # Two fresh acquires must succeed — the stale member should not
        # count against the cap (cap is 2).
        assert await acquire("user-4") is not None
        assert await acquire("user-4") is not None


# =============================================================================
# Redis outage — must degrade to "no limit enforced", never raise
# =============================================================================


class TestRedisOutage:
    @pytest.mark.asyncio
    async def test_acquire_returns_none_on_redis_outage(self, exploding_redis) -> None:
        assert await acquire("user-5") is None

    @pytest.mark.asyncio
    async def test_release_with_none_slot_is_a_noop(self, fake_redis) -> None:
        # Must not raise, must not touch Redis.
        await release("user-6", None)

    @pytest.mark.asyncio
    async def test_release_swallows_redis_errors(self, exploding_redis) -> None:
        # Must not raise even though the backing Redis explodes.
        await release("user-7", "some-slot-id")
