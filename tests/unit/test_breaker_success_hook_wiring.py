"""Coverage for `on_call_success` on the ASYNC client, and for the Format
Detection breaker wiring.

Added by the round-3 review gate. `test_breaker_client_integration.py` drives
the *sync* client only; deleting the async client's `on_call_success` call
left the entire 1112-test suite green, which is the same blind spot that let
F1 (the breaker that could never close) ship in the first place — and the
async client is the hot path (every Generator/Judge call goes through
`chat_batch` -> `_call_with_retry`).

`test_format_detection_client_is_wired_to_the_breaker` is the equivalent guard
for F4: nothing else asserts that `api/services/datasets_service.py` still
passes the three hooks.
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
    OpenRouterClient,
    Prompt,
)


def _make_response(content: str = "ok") -> Any:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content), finish_reason="stop"
            )
        ],
        model="test-model",
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )


def _status_error(code: int) -> APIStatusError:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    return APIStatusError(f"status {code}", response=httpx.Response(code, request=request), body=None)


def _timeout_error() -> APITimeoutError:
    return APITimeoutError(request=httpx.Request("GET", "https://openrouter.ai/x"))


class _FakeAsyncCompletions:
    def __init__(self, responder: Any) -> None:
        self._responder = responder
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return await self._responder(kwargs, len(self.calls) - 1)


class _FakeSyncCompletions:
    def __init__(self, responder: Any) -> None:
        self._responder = responder
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._responder(kwargs, len(self.calls) - 1)


async def _noop_close() -> None:
    return None


def _build_async(responder: Any, **hooks: Any):
    client = AsyncOpenRouterClient(api_key="dummy", **hooks)
    fake = _FakeAsyncCompletions(responder)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=fake), close=_noop_close)
    return client, fake


def _build_sync(responder: Any, **hooks: Any):
    client = OpenRouterClient(api_key="dummy", teacher_model="t", **hooks)
    fake = _FakeSyncCompletions(responder)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    return client, fake


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch: pytest.MonkeyPatch):
    original = OpenRouterClient._chat_attempt.retry.wait
    OpenRouterClient._chat_attempt.retry.wait = lambda retry_state: 0
    import ai_engine.data_gen.openrouter_client as mod

    monkeypatch.setattr(mod, "wait_exponential", lambda **_kw: (lambda _rs: 0))
    yield
    OpenRouterClient._chat_attempt.retry.wait = original


# ---- async client: on_call_success -----------------------------------------


def test_async_success_hook_fires_once_per_call():
    successes: list[int] = []

    async def responder(_kw, _i):
        return _make_response()

    client, fake = _build_async(responder, on_call_success=lambda: successes.append(1))
    asyncio.run(client.chat(system="s", user="u", model="m"))
    assert len(successes) == 1
    assert len(fake.calls) == 1


def test_async_success_hook_fires_once_per_prompt_in_batch():
    successes: list[int] = []

    async def responder(_kw, _i):
        return _make_response()

    client, _ = _build_async(responder, on_call_success=lambda: successes.append(1))
    results = asyncio.run(
        client.chat_batch(prompts=[Prompt(system="s", user=f"u{i}") for i in range(7)], model="m")
    )
    assert len(results) == 7
    assert len(successes) == 7


def test_async_success_hook_fires_once_when_call_succeeds_after_retries():
    successes: list[int] = []
    failures: list[BaseException] = []

    async def responder(_kw, i):
        if i < 2:
            raise _timeout_error()
        return _make_response()

    client, fake = _build_async(
        responder, on_call_success=lambda: successes.append(1), on_call_failure=failures.append
    )
    asyncio.run(client.chat(system="s", user="u", model="m"))
    assert len(fake.calls) == 3
    assert len(successes) == 1, "a call that succeeded after retries must report ONE success"
    assert failures == [], "retried-then-succeeded must not report a failure"


def test_async_success_hook_does_not_fire_on_terminal_failure():
    successes: list[int] = []
    failures: list[BaseException] = []

    async def responder(_kw, _i):
        raise _timeout_error()

    client, _ = _build_async(
        responder, on_call_success=lambda: successes.append(1), on_call_failure=failures.append
    )
    with pytest.raises(APITimeoutError):
        asyncio.run(client.chat(system="s", user="u", model="m"))
    assert successes == []
    assert len(failures) == 1


def test_async_success_hook_does_not_fire_on_4xx():
    successes: list[int] = []

    async def responder(_kw, _i):
        raise _status_error(400)

    client, _ = _build_async(responder, on_call_success=lambda: successes.append(1))
    with pytest.raises(APIStatusError):
        asyncio.run(client.chat(system="s", user="u", model="m"))
    assert successes == []


def test_async_raising_success_hook_is_not_reported_as_a_provider_failure():
    failures: list[BaseException] = []

    async def responder(_kw, _i):
        return _make_response()

    def boom() -> None:
        raise RuntimeError("hook bug")

    client, _ = _build_async(responder, on_call_success=boom, on_call_failure=failures.append)
    with pytest.raises(RuntimeError, match="hook bug"):
        asyncio.run(client.chat(system="s", user="u", model="m"))
    assert failures == [], "a caller-side hook bug must not be counted against the breaker"


def test_async_precheck_failure_does_not_fire_success_hook():
    successes: list[int] = []

    async def responder(_kw, _i):
        return _make_response()

    def precheck() -> None:
        raise RuntimeError("breaker open")

    client, fake = _build_async(
        responder, precheck=precheck, on_call_success=lambda: successes.append(1)
    )
    with pytest.raises(RuntimeError):
        asyncio.run(client.chat(system="s", user="u", model="m"))
    assert successes == []
    assert fake.calls == []


# ---- sync client parity -----------------------------------------------------


def test_sync_success_hook_fires_once_after_retries_and_not_on_failure():
    successes: list[int] = []
    failures: list[BaseException] = []

    def responder(_kw, i):
        if i < 2:
            raise _timeout_error()
        return _make_response()

    client, fake = _build_sync(
        responder, on_call_success=lambda: successes.append(1), on_call_failure=failures.append
    )
    client.chat(system="s", user="u")
    assert len(fake.calls) == 3
    assert len(successes) == 1
    assert failures == []


def test_sync_raising_success_hook_is_not_reported_as_a_provider_failure():
    failures: list[BaseException] = []

    def responder(_kw, _i):
        return _make_response()

    def boom() -> None:
        raise RuntimeError("hook bug")

    client, _ = _build_sync(responder, on_call_success=boom, on_call_failure=failures.append)
    with pytest.raises(RuntimeError, match="hook bug"):
        client.chat(system="s", user="u")
    assert failures == []


def test_sync_chat_raw_also_fires_success_hook():
    successes: list[int] = []

    def responder(_kw, _i):
        return _make_response()

    client, _ = _build_sync(responder, on_call_success=lambda: successes.append(1))
    client.chat_raw(messages=[{"role": "user", "content": "x"}], model="m")
    assert len(successes) == 1


# ---- F4: datasets_service wiring -------------------------------------------


def test_format_detection_client_is_wired_to_the_breaker(monkeypatch: pytest.MonkeyPatch):
    """The seed-upload Format Detection client must carry all three hooks."""
    import api.services.datasets_service as ds
    from api.services import circuit_breaker as cb

    captured: dict[str, Any] = {}

    class _Spy:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(ds, "OpenRouterClient", _Spy)
    monkeypatch.setattr(ds, "detect_and_rename", lambda **_kw: "sentinel")

    from api.schemas.enums import TaskType

    out = asyncio.run(
        ds._run_format_detection(
            rows=[{"a": 1}],
            canonical_keys={"text", "label"},
            required_keys={"text", "label"},
            task_type=TaskType.CLASSIFICATION,
            api_key="k",
            http_referer="r",
            app_title="a",
        )
    )
    assert out == "sentinel"
    assert captured.get("precheck") is cb.precheck
    assert captured.get("on_call_failure") is cb.on_failure
    assert captured.get("on_call_success") is cb.record_success
