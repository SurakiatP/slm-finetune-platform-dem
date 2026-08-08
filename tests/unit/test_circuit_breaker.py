"""Unit tests for `api/services/circuit_breaker.py`.

All Redis interaction is faked with `fakeredis` — one `FakeServer` shared by
a sync (`FakeStrictRedis`) and an async (`FakeAsyncRedis`) client so the
worker-side (`precheck`/`record_*`) and API-side (`assert_closed`) surfaces
observe the exact same in-memory state, the same way the real sync worker
client and async API client both point at the same real Redis instance.

Threshold/window come from `Settings` defaults (5 failures / 60s open) unless
a test overrides them via `monkeypatch`, to keep the concurrency test fast.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import fakeredis
import pytest
from fastapi import HTTPException

from api.services import circuit_breaker as cb

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def fake_breaker_redis(monkeypatch: pytest.MonkeyPatch):
    """Patch both the sync and async client factories in `circuit_breaker`
    to point at the same in-memory fakeredis server, and clear any leftover
    keys between tests."""
    server = fakeredis.FakeServer()
    sync_client = fakeredis.FakeStrictRedis(server=server, decode_responses=True)
    async_client = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)

    monkeypatch.setattr(cb, "_sync_client", lambda: sync_client)
    monkeypatch.setattr(cb, "get_redis_client", lambda: async_client)

    sync_client.flushall()
    return sync_client


def test_four_failures_stay_closed(fake_breaker_redis) -> None:
    for _ in range(4):
        cb.record_failure()
    state, retry_after = cb.state()
    assert state == "closed"
    assert retry_after == 0


def test_fifth_consecutive_failure_opens(fake_breaker_redis) -> None:
    for _ in range(5):
        cb.record_failure()
    state, retry_after = cb.state()
    assert state == "open"
    assert retry_after > 0


def test_success_before_threshold_resets_count(fake_breaker_redis) -> None:
    for _ in range(4):
        cb.record_failure()
    cb.record_success()
    # Four more failures after the reset must NOT be enough to trip it —
    # proves the counter actually went back to zero, not just "didn't open
    # yet".
    for _ in range(4):
        cb.record_failure()
    state, _ = cb.state()
    assert state == "closed"


async def test_assert_closed_raises_503_with_retry_after_while_open(
    fake_breaker_redis,
) -> None:
    for _ in range(5):
        cb.record_failure()
    assert cb.state()[0] == "open"

    with pytest.raises(HTTPException) as exc_info:
        await cb.assert_closed()

    assert exc_info.value.status_code == 503
    assert "Retry-After" in exc_info.value.headers
    assert int(exc_info.value.headers["Retry-After"]) > 0


async def test_assert_closed_passes_while_closed(fake_breaker_redis) -> None:
    await cb.assert_closed()  # must not raise


def test_precheck_passes_while_closed(fake_breaker_redis) -> None:
    cb.precheck()  # must not raise


def test_precheck_raises_while_open(fake_breaker_redis) -> None:
    for _ in range(5):
        cb.record_failure()
    with pytest.raises(cb.CircuitOpenError):
        cb.precheck()


def test_half_open_admits_exactly_one_of_ten_concurrent_prechecks(
    fake_breaker_redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = cb.get_settings()
    # Trip the breaker, then fast-forward past the open window by forging an
    # `opened_at` timestamp in the past instead of sleeping in the test.
    for _ in range(5):
        cb.record_failure()
    past = time.time() - settings.openrouter_breaker_open_seconds - 1
    fake_breaker_redis.set(cb._OPENED_AT_KEY, str(past))
    assert cb.state()[0] == "half_open"

    admitted = []
    refused = []
    lock = threading.Lock()

    def _attempt() -> None:
        try:
            cb.precheck()
        except cb.CircuitOpenError:
            with lock:
                refused.append(1)
        else:
            with lock:
                admitted.append(1)

    threads = [threading.Thread(target=_attempt) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(admitted) == 1
    assert len(refused) == 9


def test_half_open_probe_success_closes_breaker(fake_breaker_redis) -> None:
    settings = cb.get_settings()
    for _ in range(5):
        cb.record_failure()
    past = time.time() - settings.openrouter_breaker_open_seconds - 1
    fake_breaker_redis.set(cb._OPENED_AT_KEY, str(past))
    assert cb.state()[0] == "half_open"

    cb.precheck()  # claim the probe slot
    cb.record_success()

    state, retry_after = cb.state()
    assert state == "closed"
    assert retry_after == 0
    # Reset for real: a handful of failures afterwards should need the full
    # threshold again, proving the counter was cleared, not left at 4.
    for _ in range(4):
        cb.record_failure()
    assert cb.state()[0] == "closed"


def test_half_open_probe_failure_reopens_for_full_window(fake_breaker_redis) -> None:
    settings = cb.get_settings()
    for _ in range(5):
        cb.record_failure()
    past = time.time() - settings.openrouter_breaker_open_seconds - 1
    fake_breaker_redis.set(cb._OPENED_AT_KEY, str(past))
    assert cb.state()[0] == "half_open"

    cb.precheck()  # claim the probe slot
    cb.record_failure()  # the probe's own call failed

    state, retry_after = cb.state()
    assert state == "open"
    # Re-opened for the *full* window, not the (already-elapsed) old one.
    assert retry_after >= settings.openrouter_breaker_open_seconds - 1


def test_on_failure_ignores_non_breaker_exceptions(
    fake_breaker_redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cb, "is_breaker_failure", lambda exc: False)
    for _ in range(10):
        cb.on_failure(RuntimeError("not an outage"))
    assert cb.state()[0] == "closed"
    # Nothing should even have incremented the counter.
    assert cb._sync_client().get(cb._FAILURES_KEY) is None


def test_on_failure_counts_breaker_failures(
    fake_breaker_redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cb, "is_breaker_failure", lambda exc: True)
    for _ in range(5):
        cb.on_failure(RuntimeError("outage-shaped"))
    assert cb.state()[0] == "open"


# ---- Redis-outage fail-open behavior ----------------------------------------


class _ExplodingRedis:
    """Every data call raises; `close()` behaves like the real redis-py
    client, which only tears down a local connection pool and does not
    itself talk to the network — it must not raise just because the server
    is unreachable."""

    def close(self) -> None:
        return None

    def __getattr__(self, name: str):
        def _boom(*args, **kwargs):
            raise ConnectionError("redis is down")

        return _boom


class _ExplodingAsyncRedis:
    async def aclose(self) -> None:
        return None

    def __getattr__(self, name: str):
        async def _boom(*args, **kwargs):
            raise ConnectionError("redis is down")

        return _boom


def test_redis_outage_degrades_to_closed_on_every_sync_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cb, "_sync_client", lambda: _ExplodingRedis())

    cb.record_failure()  # must not raise
    cb.record_success()  # must not raise
    state, retry_after = cb.state()
    assert (state, retry_after) == ("closed", 0)
    cb.precheck()  # must not raise
    cb.on_failure(cb.CircuitOpenError(1))  # must not raise (also not a breaker failure)


async def test_redis_outage_degrades_to_closed_for_assert_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cb, "get_redis_client", lambda: _ExplodingAsyncRedis())
    await cb.assert_closed()  # must not raise


# ---- Worker-image import surface --------------------------------------------

_BLOCK_JWT_AND_IMPORT = """
import sys, importlib.abc


class _NoPyJWT(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name == "jwt" or name.startswith("jwt."):
            raise ModuleNotFoundError(
                "No module named 'jwt' (simulating the worker image)"
            )
        return None


sys.meta_path.insert(0, _NoPyJWT())
import api.services.circuit_breaker
print("IMPORTED")
"""


def test_module_imports_without_pyjwt() -> None:
    """Mirrors `tests/unit/test_worker_import_surface.py`'s technique: the
    Celery worker image ships no PyJWT, so this module (imported by the
    OpenRouter client's hooks) must not pull in `api.core.auth` at module
    scope, directly or transitively."""
    result = subprocess.run(
        [sys.executable, "-c", _BLOCK_JWT_AND_IMPORT],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert "IMPORTED" in result.stdout, (
        "api.services.circuit_breaker cannot be imported in the worker image.\n"
        f"stderr:\n{result.stderr}"
    )
