"""Unit tests for the circuit-breaker injection points on both OpenRouter clients.

These hooks (`precheck`, `on_call_failure`) and `is_breaker_failure` are the
*only* surface a Redis-backed breaker (built elsewhere, in
`api/services/circuit_breaker.py`) needs from this module. This module itself
stays stateless — no counting, no Redis — so these tests only prove the
wiring: call ordering, and "exactly once per logical call" for the failure
hook, for both the sync and the async client.

Mocks the underlying OpenAI SDK `chat.completions.create` directly — no real
network, no real sleeping (tenacity's wait is patched to 0 for both clients).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import APIStatusError, APITimeoutError

from ai_engine.data_gen.openrouter_client import (
    AsyncOpenRouterClient,
    ChatResult,
    OpenRouterClient,
    is_breaker_failure,
)


# --- shared fakes / helpers -------------------------------------------------


def _make_response(content: str = "ok") -> Any:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason="stop",
            )
        ],
        model="test-model",
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )


def _status_error(status_code: int) -> APIStatusError:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(status_code, request=request)
    return APIStatusError(f"status {status_code}", response=response, body=None)


def _timeout_error() -> APITimeoutError:
    return APITimeoutError(request=httpx.Request("GET", "https://openrouter.ai/api/v1/chat/completions"))


class _FakeSyncCompletions:
    """Records calls; `responder(kwargs, call_index)` decides the outcome."""

    def __init__(self, responder: Any) -> None:
        self._responder = responder
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._responder(kwargs, len(self.calls) - 1)


class _FakeAsyncCompletions:
    """Async twin of `_FakeSyncCompletions`."""

    def __init__(self, responder: Any) -> None:
        self._responder = responder
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return await self._responder(kwargs, len(self.calls) - 1)


async def _noop_close() -> None:  # pragma: no cover — trivial
    return None


def _build_sync_client(responder: Any, **hooks: Any) -> tuple[OpenRouterClient, _FakeSyncCompletions]:
    client = OpenRouterClient(api_key="dummy", teacher_model="teacher-model", **hooks)
    fake = _FakeSyncCompletions(responder)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=fake))  # type: ignore[assignment]
    return client, fake


def _build_async_client(responder: Any, **hooks: Any) -> tuple[AsyncOpenRouterClient, _FakeAsyncCompletions]:
    client = AsyncOpenRouterClient(api_key="dummy", **hooks)
    fake = _FakeAsyncCompletions(responder)
    client._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=fake),
        close=_noop_close,
    )
    return client, fake


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Eliminate tenacity's real sleeping in both clients for this test module.

    The sync client's `@retry(...)` decorator is evaluated once at import
    time, so `wait_exponential(...)` is already baked into a `Retrying`
    object hanging off the function (`OpenRouterClient._chat_attempt.retry`)
    — patch that object's `.wait` directly. The async client instead builds
    `AsyncRetrying(wait=wait_exponential(...))` fresh inside `_call_with_retry`
    on every call, referencing the module-global `wait_exponential` name, so
    patching that module attribute (as the existing async test suite already
    does) is enough for it.
    """
    original_sync_wait = OpenRouterClient._chat_attempt.retry.wait  # type: ignore[attr-defined]
    OpenRouterClient._chat_attempt.retry.wait = lambda retry_state: 0  # type: ignore[attr-defined]

    import ai_engine.data_gen.openrouter_client as mod

    monkeypatch.setattr(mod, "wait_exponential", lambda **_kw: (lambda _retry_state: 0))

    yield

    OpenRouterClient._chat_attempt.retry.wait = original_sync_wait  # type: ignore[attr-defined]


# --- is_breaker_failure ------------------------------------------------------


def test_is_breaker_failure_true_for_retryable_tuple():
    assert is_breaker_failure(_timeout_error()) is True


def test_is_breaker_failure_true_for_5xx_status():
    assert is_breaker_failure(_status_error(500)) is True
    assert is_breaker_failure(_status_error(503)) is True
    assert is_breaker_failure(_status_error(599)) is True


def test_is_breaker_failure_false_for_4xx_status():
    assert is_breaker_failure(_status_error(400)) is False
    assert is_breaker_failure(_status_error(404)) is False
    assert is_breaker_failure(_status_error(429)) is False  # RateLimitError subclass, not raw 429 status_error path


def test_is_breaker_failure_false_for_unrelated_exception():
    assert is_breaker_failure(ValueError("boom")) is False


# --- sync client: precheck ---------------------------------------------------


def test_sync_precheck_raising_blocks_all_sdk_calls():
    class BreakerOpen(Exception):
        pass

    def precheck() -> None:
        raise BreakerOpen("breaker is open")

    def responder(_kwargs: dict[str, Any], _idx: int) -> Any:
        return _make_response()

    client, fake = _build_sync_client(responder, precheck=precheck)

    with pytest.raises(BreakerOpen):
        client.chat(system="s", user="u")

    assert fake.calls == []


def test_sync_precheck_runs_before_every_attempt():
    """A precheck that stops raising (with a retryable error) after the first
    call lets tenacity's retry loop recover, proving precheck runs again on
    each retry attempt — not just once for the whole logical call."""
    state = {"precheck_calls": 0}

    def precheck() -> None:
        state["precheck_calls"] += 1
        if state["precheck_calls"] < 2:
            raise _timeout_error()  # retryable: tenacity tries again

    def responder(_kwargs: dict[str, Any], _idx: int) -> Any:
        return _make_response(content="recovered")

    client, fake = _build_sync_client(responder, precheck=precheck)

    result = client.chat(system="s", user="u")

    assert result.content == "recovered"
    assert state["precheck_calls"] == 2  # ran again on the retried attempt
    assert len(fake.calls) == 1  # SDK only reached once precheck passed


# --- sync client: on_call_failure -------------------------------------------


def test_sync_on_call_failure_fires_once_for_timeout_after_retries_exhausted():
    def responder(_kwargs: dict[str, Any], _idx: int) -> Any:
        raise _timeout_error()

    failures: list[BaseException] = []
    client, fake = _build_sync_client(responder, on_call_failure=failures.append)

    with pytest.raises(APITimeoutError):
        client.chat(system="s", user="u")

    assert len(fake.calls) == 4  # stop_after_attempt(4): all attempts consumed
    assert len(failures) == 1
    assert isinstance(failures[0], APITimeoutError)


def test_sync_on_call_failure_fires_once_for_503():
    def responder(_kwargs: dict[str, Any], _idx: int) -> Any:
        raise _status_error(503)

    failures: list[BaseException] = []
    client, fake = _build_sync_client(responder, on_call_failure=failures.append)

    with pytest.raises(APIStatusError):
        client.chat_raw(messages=[{"role": "user", "content": "hi"}], model="m")

    # 503 is not in _RETRYABLE, so tenacity doesn't retry it — one attempt,
    # one hook call.
    assert len(fake.calls) == 1
    assert len(failures) == 1


@pytest.mark.parametrize("status_code", [400, 404])
def test_sync_on_call_failure_does_not_fire_for_4xx(status_code: int):
    def responder(_kwargs: dict[str, Any], _idx: int) -> Any:
        raise _status_error(status_code)

    failures: list[BaseException] = []
    client, fake = _build_sync_client(responder, on_call_failure=failures.append)

    with pytest.raises(APIStatusError):
        client.chat(system="s", user="u")

    assert len(fake.calls) == 1
    assert failures == []


def test_sync_no_hooks_behaves_as_before():
    def responder(_kwargs: dict[str, Any], _idx: int) -> Any:
        return _make_response(content="hello")

    client, fake = _build_sync_client(responder)

    result = client.chat(system="s", user="u")

    assert isinstance(result, ChatResult)
    assert result.content == "hello"
    assert len(fake.calls) == 1


def test_sync_no_hooks_failure_still_propagates_cleanly():
    def responder(_kwargs: dict[str, Any], _idx: int) -> Any:
        raise _status_error(500)

    client, fake = _build_sync_client(responder)

    with pytest.raises(APIStatusError):
        client.chat(system="s", user="u")

    assert len(fake.calls) == 1


# --- async client: precheck --------------------------------------------------


@pytest.mark.asyncio
async def test_async_precheck_raising_blocks_all_sdk_calls():
    class BreakerOpen(Exception):
        pass

    def precheck() -> None:
        raise BreakerOpen("breaker is open")

    async def responder(_kwargs: dict[str, Any], _idx: int) -> Any:
        return _make_response()

    client, fake = _build_async_client(responder, precheck=precheck)

    with pytest.raises(BreakerOpen):
        await client.chat(system="s", user="u", model="m")

    assert fake.calls == []


@pytest.mark.asyncio
async def test_async_precheck_raise_is_not_swallowed_by_chat_batch():
    """chat_batch's return_exceptions=True must still surface a precheck
    failure per-prompt as an exception instance, not silently drop it."""

    class BreakerOpen(Exception):
        pass

    def precheck() -> None:
        raise BreakerOpen("breaker is open")

    async def responder(_kwargs: dict[str, Any], _idx: int) -> Any:
        return _make_response()

    from ai_engine.data_gen.openrouter_client import Prompt

    client, fake = _build_async_client(responder, precheck=precheck)
    results = await client.chat_batch(prompts=[Prompt(system="s", user="u")], model="m")

    assert len(results) == 1
    assert isinstance(results[0], BreakerOpen)
    assert fake.calls == []


# --- async client: on_call_failure ------------------------------------------


@pytest.mark.asyncio
async def test_async_on_call_failure_fires_once_for_timeout_after_retries_exhausted():
    async def responder(_kwargs: dict[str, Any], _idx: int) -> Any:
        raise _timeout_error()

    failures: list[BaseException] = []
    client, fake = _build_async_client(responder, on_call_failure=failures.append)

    with pytest.raises(APITimeoutError):
        await client.chat(system="s", user="u", model="m")

    assert len(fake.calls) == 4  # stop_after_attempt(4): all attempts consumed
    assert len(failures) == 1
    assert isinstance(failures[0], APITimeoutError)


@pytest.mark.asyncio
async def test_async_on_call_failure_fires_once_for_503():
    async def responder(_kwargs: dict[str, Any], _idx: int) -> Any:
        raise _status_error(503)

    failures: list[BaseException] = []
    client, fake = _build_async_client(responder, on_call_failure=failures.append)

    with pytest.raises(APIStatusError):
        await client.chat(system="s", user="u", model="m")

    # 503 is not in _RETRYABLE, so tenacity doesn't retry it — one attempt,
    # one hook call.
    assert len(fake.calls) == 1
    assert len(failures) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 404])
async def test_async_on_call_failure_does_not_fire_for_4xx(status_code: int):
    async def responder(_kwargs: dict[str, Any], _idx: int) -> Any:
        raise _status_error(status_code)

    failures: list[BaseException] = []
    client, fake = _build_async_client(responder, on_call_failure=failures.append)

    with pytest.raises(APIStatusError):
        await client.chat(system="s", user="u", model="m")

    assert len(fake.calls) == 1
    assert failures == []


@pytest.mark.asyncio
async def test_async_no_hooks_behaves_as_before():
    async def responder(_kwargs: dict[str, Any], _idx: int) -> Any:
        return _make_response(content="hello")

    client, fake = _build_async_client(responder)

    result = await client.chat(system="s", user="u", model="m")

    assert isinstance(result, ChatResult)
    assert result.content == "hello"
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_async_no_hooks_failure_still_propagates_cleanly():
    async def responder(_kwargs: dict[str, Any], _idx: int) -> Any:
        raise _status_error(500)

    client, fake = _build_async_client(responder)

    with pytest.raises(APIStatusError):
        await client.chat(system="s", user="u", model="m")

    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_async_on_call_failure_fires_once_per_prompt_in_batch():
    """Batch of 3 prompts, all timing out: hook fires once per logical call
    (3 total), not once per retry attempt (12 total)."""
    from ai_engine.data_gen.openrouter_client import Prompt

    async def responder(_kwargs: dict[str, Any], _idx: int) -> Any:
        raise _timeout_error()

    failures: list[BaseException] = []
    client, fake = _build_async_client(responder, on_call_failure=failures.append)

    prompts = [Prompt(system="s", user=f"u{i}") for i in range(3)]
    results = await client.chat_batch(prompts=prompts, model="m", concurrency=3)

    assert len(results) == 3
    assert all(isinstance(r, APITimeoutError) for r in results)
    assert len(fake.calls) == 3 * 4  # each prompt exhausts 4 attempts
    assert len(failures) == 3  # exactly one hook call per logical (per-prompt) call
