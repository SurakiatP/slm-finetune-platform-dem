"""End-to-end tests for the breaker↔OpenRouter-client wiring.

These exist because `tests/unit/test_circuit_breaker.py` calls
`record_success()` / `record_failure()` **directly**, which made every one of
its assertions pass while nothing in production ever called `record_success`
at all. The client had `precheck` and `on_call_failure` hooks but no success
hook, so `_FAILURES_KEY` was INCR-only: the breaker counted *cumulative*
lifetime failures instead of consecutive ones, and once open it could never
close again — it parked in `half_open` forever, admitting one call per window
until somebody flushed Redis by hand.

1105 green tests did not see it. So these tests deliberately drive **real
`client.chat(...)` calls** through a client wired exactly the way
`workers/tasks/data_generation.py::_run_generator` wires it, and assert on
the breaker state that results. If the wiring is ever dropped again, these
fail; the direct-call tests would not.
"""

from __future__ import annotations

import time
from typing import Any

import fakeredis
import pytest
from openai import APITimeoutError

from ai_engine.data_gen.openrouter_client import OpenRouterClient
from api.services import circuit_breaker as cb


@pytest.fixture
def fake_breaker_redis(monkeypatch: pytest.MonkeyPatch):
    server = fakeredis.FakeServer()
    sync_client = fakeredis.FakeStrictRedis(server=server, decode_responses=True)
    async_client = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    monkeypatch.setattr(cb, "_sync_client", lambda: sync_client)
    monkeypatch.setattr(cb, "get_redis_client", lambda: async_client)
    sync_client.flushall()
    return sync_client


class _Resp:
    """Minimal stand-in for an OpenAI SDK chat-completion response."""

    def __init__(self) -> None:
        message = type("M", (), {"content": '{"ok": true}'})()
        self.choices = [type("C", (), {"message": message, "finish_reason": "stop"})()]
        self.usage = type("U", (), {"prompt_tokens": 3, "completion_tokens": 4})()
        self.model = "test/model"


class _ScriptedCompletions:
    """Returns/raises per a script, so a test can interleave outcomes."""

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.calls = 0

    def create(self, **_kwargs: Any) -> _Resp:
        self.calls += 1
        outcome = self._script.pop(0) if self._script else _Resp()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _client_wired_like_the_worker(script: list[Any]) -> tuple[OpenRouterClient, _ScriptedCompletions]:
    """Build a client with the exact hook set `_run_generator` passes."""
    client = OpenRouterClient(
        api_key="test-key",
        teacher_model="test/model",
        precheck=cb.precheck,
        on_call_failure=cb.on_failure,
        on_call_success=cb.record_success,
    )
    completions = _ScriptedCompletions(script)
    client._client = type(  # type: ignore[assignment]
        "FakeSDK", (), {"chat": type("Chat", (), {"completions": completions})()}
    )()
    return client, completions


@pytest.fixture(autouse=True)
def _no_retry_sleeps(monkeypatch: pytest.MonkeyPatch):
    """Strip tenacity's exponential backoff so failures resolve instantly."""
    from tenacity import wait_none

    monkeypatch.setattr(OpenRouterClient._chat_attempt.retry, "wait", wait_none())


def _chat(client: OpenRouterClient) -> Any:
    return client.chat(system="s", user="u")


def _timeout() -> APITimeoutError:
    return APITimeoutError(request=None)  # type: ignore[arg-type]


def test_an_intervening_success_resets_the_failure_count(fake_breaker_redis) -> None:
    """4 failures + 1 success + 1 failure must leave the counter at 1, not 5.

    This is the difference between "consecutive" (what the design and the
    docs promise) and "cumulative" (what an INCR-only counter actually does).
    """
    # Each failing logical call exhausts 4 retry attempts internally.
    script: list[Any] = [_timeout()] * 16 + [_Resp()] + [_timeout()] * 4
    client, _ = _client_wired_like_the_worker(script)

    for _ in range(4):
        with pytest.raises(APITimeoutError):
            _chat(client)
    assert fake_breaker_redis.get(cb._FAILURES_KEY) == "4"

    _chat(client)  # a real success through the real hook path
    assert fake_breaker_redis.get(cb._FAILURES_KEY) in (None, "0")

    with pytest.raises(APITimeoutError):
        _chat(client)
    assert fake_breaker_redis.get(cb._FAILURES_KEY) == "1"
    assert cb.state()[0] == "closed"


def test_fifth_consecutive_failure_opens_the_breaker(fake_breaker_redis) -> None:
    client, _ = _client_wired_like_the_worker([_timeout()] * 20)
    for _ in range(5):
        with pytest.raises(APITimeoutError):
            _chat(client)
    assert cb.state()[0] == "open"


def test_a_successful_half_open_probe_closes_the_breaker(
    fake_breaker_redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probe's success must actually close the breaker.

    Before the success hook existed, nothing cleared `_OPENED_AT_KEY`, so the
    breaker stayed `half_open` permanently — one admitted call per window,
    forever.
    """
    monkeypatch.setattr(
        cb.get_settings(), "openrouter_breaker_open_seconds", 1, raising=False
    )
    client, _ = _client_wired_like_the_worker([_timeout()] * 20 + [_Resp()])

    for _ in range(5):
        with pytest.raises(APITimeoutError):
            _chat(client)
    assert cb.state()[0] == "open"

    time.sleep(1.1)
    assert cb.state()[0] == "half_open"

    _chat(client)  # the probe succeeds
    assert cb.state()[0] == "closed", "a successful probe did not close the breaker"


def test_ordinary_calls_resume_after_a_successful_probe(
    fake_breaker_redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The call *after* a successful probe must not be refused."""
    monkeypatch.setattr(
        cb.get_settings(), "openrouter_breaker_open_seconds", 1, raising=False
    )
    client, completions = _client_wired_like_the_worker([_timeout()] * 20 + [_Resp(), _Resp()])

    for _ in range(5):
        with pytest.raises(APITimeoutError):
            _chat(client)
    time.sleep(1.1)
    _chat(client)  # probe

    calls_before = completions.calls
    _chat(client)  # must not raise CircuitOpenError
    assert completions.calls == calls_before + 1
