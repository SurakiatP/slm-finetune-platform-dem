"""Unit tests for AsyncOpenRouterClient.embed.

Mocks `AsyncOpenAI.embeddings.create` directly — no real network. Verifies:
  • happy path — ordered vectors + prompt tokens
  • out-of-order response `data` indices get re-sorted
  • empty `texts` short-circuits: zero SDK calls, no hooks fired
  • a retryable error then success retries internally (fast — no real sleep)
  • breaker hooks fire exactly once: on_call_success on success,
    on_call_failure on an exhausted APIConnectionError, and NOT on a
    non-retryable 400 APIStatusError
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import APIConnectionError, APIStatusError

from ai_engine.data_gen.openrouter_client import (
    AsyncOpenRouterClient,
    EmbeddingResult,
)


def _make_response(vectors: list[list[float]], model: str = "test-embed-model", prompt_tokens: int | None = 7) -> Any:
    return SimpleNamespace(
        data=[SimpleNamespace(embedding=v, index=i) for i, v in enumerate(vectors)],
        model=model,
        usage=SimpleNamespace(prompt_tokens=prompt_tokens) if prompt_tokens is not None else None,
    )


class _FakeEmbeddings:
    def __init__(self, responder: Any) -> None:
        self._responder = responder
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if callable(self._responder):
            return await self._responder(kwargs, len(self.calls) - 1)
        return self._responder


async def _noop_close() -> None:  # pragma: no cover — trivial
    return None


def _connection_error() -> APIConnectionError:
    return APIConnectionError(request=httpx.Request("POST", "https://openrouter.ai/api/v1/embeddings"))


def _status_error(status_code: int) -> APIStatusError:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/embeddings")
    response = httpx.Response(status_code, request=request)
    return APIStatusError(f"status {status_code}", response=response, body=None)


@pytest.fixture
def client_factory(monkeypatch: pytest.MonkeyPatch):
    """Build a client whose underlying AsyncOpenAI.embeddings is replaced with a fake."""

    def _make(responder: Any, **hooks: Any) -> tuple[AsyncOpenRouterClient, _FakeEmbeddings]:
        c = AsyncOpenRouterClient(api_key="dummy", **hooks)
        fake = _FakeEmbeddings(responder)
        c._client = SimpleNamespace(  # type: ignore[assignment]
            embeddings=fake,
            close=_noop_close,
        )
        return c, fake

    return _make


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Eliminate tenacity's real sleeping for this test module (async client
    builds AsyncRetrying fresh per-call referencing the module-global
    `wait_exponential` name — patching that module attribute is enough, same
    pattern as test_async_openrouter_client.py."""
    import ai_engine.data_gen.openrouter_client as mod

    monkeypatch.setattr(mod, "wait_exponential", lambda **_kw: (lambda _retry_state: 0))


@pytest.mark.asyncio
async def test_embed_happy_path_returns_ordered_vectors_and_prompt_tokens(client_factory):
    async def responder(kwargs: dict[str, Any], idx: int) -> Any:
        return _make_response(vectors=[[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]], model="embed-m", prompt_tokens=42)

    client, fake = client_factory(responder)
    result = await client.embed(texts=["a", "b", "c"], model="embed-m")

    assert isinstance(result, EmbeddingResult)
    assert result.vectors == [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]
    assert result.model == "embed-m"
    assert result.prompt_tokens == 42
    assert len(fake.calls) == 1
    assert fake.calls[0]["model"] == "embed-m"
    assert fake.calls[0]["input"] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_embed_reorders_out_of_order_response_indices(client_factory):
    async def responder(kwargs: dict[str, Any], idx: int) -> Any:
        # Response comes back with indices out of order; vectors tagged so we
        # can assert the final ordering follows `index`, not response order.
        return SimpleNamespace(
            data=[
                SimpleNamespace(embedding=[9.0], index=2),
                SimpleNamespace(embedding=[7.0], index=0),
                SimpleNamespace(embedding=[8.0], index=1),
            ],
            model="embed-m",
            usage=SimpleNamespace(prompt_tokens=5),
        )

    client, _fake = client_factory(responder)
    result = await client.embed(texts=["x", "y", "z"], model="embed-m")

    assert result.vectors == [[7.0], [8.0], [9.0]]


@pytest.mark.asyncio
async def test_embed_empty_texts_short_circuits_no_sdk_call_no_hooks(client_factory):
    successes: list[None] = []
    failures: list[BaseException] = []
    client, fake = client_factory(
        _make_response([[1.0]]),
        on_call_success=lambda: successes.append(None),
        on_call_failure=failures.append,
    )

    result = await client.embed(texts=[], model="embed-m")

    assert result == EmbeddingResult(vectors=[], model="embed-m", prompt_tokens=0)
    assert fake.calls == []
    assert successes == []
    assert failures == []


@pytest.mark.asyncio
async def test_embed_retries_connection_error_then_succeeds(client_factory):
    state = {"calls": 0}

    async def responder(kwargs: dict[str, Any], idx: int) -> Any:
        state["calls"] += 1
        if state["calls"] < 2:
            raise _connection_error()
        return _make_response(vectors=[[1.0, 1.0]], model="embed-m", prompt_tokens=3)

    client, fake = client_factory(responder)
    result = await client.embed(texts=["only"], model="embed-m")

    assert result.vectors == [[1.0, 1.0]]
    assert state["calls"] == 2
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_embed_on_call_success_fires_once_on_success(client_factory):
    successes: list[None] = []
    client, _fake = client_factory(
        _make_response([[1.0]]),
        on_call_success=lambda: successes.append(None),
    )

    await client.embed(texts=["a"], model="embed-m")

    assert len(successes) == 1


@pytest.mark.asyncio
async def test_embed_on_call_failure_fires_once_for_exhausted_connection_error(client_factory):
    async def responder(kwargs: dict[str, Any], idx: int) -> Any:
        raise _connection_error()

    failures: list[BaseException] = []
    successes: list[None] = []
    client, fake = client_factory(
        responder,
        on_call_failure=failures.append,
        on_call_success=lambda: successes.append(None),
    )

    with pytest.raises(APIConnectionError):
        await client.embed(texts=["a"], model="embed-m")

    assert len(fake.calls) == 4  # stop_after_attempt(4): all attempts consumed
    assert len(failures) == 1
    assert isinstance(failures[0], APIConnectionError)
    assert successes == []


@pytest.mark.asyncio
async def test_embed_on_call_failure_does_not_fire_for_400(client_factory):
    async def responder(kwargs: dict[str, Any], idx: int) -> Any:
        raise _status_error(400)

    failures: list[BaseException] = []
    successes: list[None] = []
    client, fake = client_factory(
        responder,
        on_call_failure=failures.append,
        on_call_success=lambda: successes.append(None),
    )

    with pytest.raises(APIStatusError):
        await client.embed(texts=["a"], model="embed-m")

    # 400 is not in _RETRYABLE, so tenacity doesn't retry it — one attempt.
    assert len(fake.calls) == 1
    assert failures == []
    assert successes == []
