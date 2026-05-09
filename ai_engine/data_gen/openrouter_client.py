"""Thin wrappers around the OpenAI SDK pointed at OpenRouter (ADR-003).

Two clients live here, intentionally co-located (ADR-007):

  • `OpenRouterClient` — sync, single-shot. Used by the Format Detection
    upload-time call and the QA + PDF multimodal call. Wrap with
    `asyncio.to_thread(...)` if invoked from an async FastAPI handler.

  • `AsyncOpenRouterClient` — async, batch primitive. Used by the
    Generator and Judge stages of the SDG loop, where 100s of concurrent
    calls per loop iteration are the norm.

Retries are handled here (tenacity) rather than at every call site.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    OpenAI,
    RateLimitError,
)
from tenacity import (
    AsyncRetrying,
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Errors worth retrying. NOT retried: 4xx other than 429 (handled below by RateLimitError).
_RETRYABLE = (APIConnectionError, APITimeoutError, RateLimitError)


@dataclass(frozen=True)
class ChatResult:
    content: str
    model: str
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None


# --- Sync client (single-shot calls) ---------------------------------------


class OpenRouterClient:
    """Sync OpenRouter client. One instance per process is enough."""

    def __init__(
        self,
        api_key: str,
        teacher_model: str,
        http_referer: str = "http://localhost:8000",
        app_title: str = "slm-platform",
        timeout_seconds: float = 60.0,
    ) -> None:
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is empty — set it in .env.")
        self._client = OpenAI(
            api_key=api_key,
            base_url=OPENROUTER_BASE_URL,
            timeout=timeout_seconds,
            default_headers={
                # OpenRouter analytics / billing attribution.
                "HTTP-Referer": http_referer,
                "X-Title": app_title,
            },
        )
        self._teacher_model = teacher_model

    @property
    def teacher_model(self) -> str:
        return self._teacher_model

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(_RETRYABLE),
        before_sleep=before_sleep_log(log, logging.WARNING),
        reraise=True,
    )
    def chat(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
        temperature: float = 0.9,
        max_tokens: int | None = 4096,
        response_format: dict[str, Any] | None = None,
    ) -> ChatResult:
        """One chat completion call. Retries network/timeout/rate-limit errors only."""
        try:
            resp = self._client.chat.completions.create(
                model=model or self._teacher_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )
        except APIStatusError as exc:
            log.error(
                "OpenRouter API error (status=%s, model=%s): %s",
                exc.status_code,
                model or self._teacher_model,
                exc.message,
            )
            raise
        return _to_chat_result(resp)

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(_RETRYABLE),
        before_sleep=before_sleep_log(log, logging.WARNING),
        reraise=True,
    )
    def chat_raw(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.9,
        max_tokens: int | None = 4096,
        response_format: dict[str, Any] | None = None,
    ) -> ChatResult:
        """Chat with caller-supplied messages.

        Used for multimodal calls (e.g. PDF + text) where we need an
        OpenAI-compatible content array per message instead of plain
        system/user strings.
        """
        try:
            resp = self._client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )
        except APIStatusError as exc:
            log.error(
                "OpenRouter API error (status=%s, model=%s): %s",
                exc.status_code,
                model,
                exc.message,
            )
            raise
        return _to_chat_result(resp)


# --- Async client (batch primitives) ---------------------------------------


@dataclass(frozen=True)
class Prompt:
    """A single (system, user) pair for the Generator/Judge batch primitives."""

    system: str
    user: str


class AsyncOpenRouterClient:
    """Async OpenRouter client. Primary primitive: `chat_batch`.

    One instance per Celery task is fine — the underlying `httpx.AsyncClient`
    held by `AsyncOpenAI` is connection-pooled. Always close the client
    when done (or use the async context-manager form).
    """

    def __init__(
        self,
        api_key: str,
        http_referer: str = "http://localhost:8000",
        app_title: str = "slm-platform",
        timeout_seconds: float = 120.0,
    ) -> None:
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is empty — set it in .env.")
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=OPENROUTER_BASE_URL,
            timeout=timeout_seconds,
            default_headers={
                "HTTP-Referer": http_referer,
                "X-Title": app_title,
            },
        )

    async def aclose(self) -> None:
        """Release pooled connections. Safe to call multiple times."""
        await self._client.close()

    async def __aenter__(self) -> "AsyncOpenRouterClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def chat(
        self,
        *,
        system: str,
        user: str,
        model: str,
        temperature: float = 0.9,
        max_tokens: int | None = 4096,
        response_format: dict[str, Any] | None = None,
    ) -> ChatResult:
        """One async chat completion. Retried on connect/timeout/429."""
        return await self._call_with_retry(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )

    async def chat_batch(
        self,
        *,
        prompts: list[Prompt],
        model: str,
        temperature: float = 0.9,
        max_tokens: int | None = 4096,
        response_format: dict[str, Any] | None = None,
        concurrency: int = 100,
    ) -> list[ChatResult | Exception]:
        """Fire `prompts` concurrently under a semaphore.

        Returns results in input order. Failed calls return their exception
        instance instead of raising — the caller filters/logs. Per-call
        retries are applied inside, so what reaches the caller is the
        *final* outcome after retries.
        """
        if not prompts:
            return []
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")

        semaphore = asyncio.Semaphore(concurrency)

        async def _one(p: Prompt) -> ChatResult:
            async with semaphore:
                return await self._call_with_retry(
                    messages=[
                        {"role": "system", "content": p.system},
                        {"role": "user", "content": p.user},
                    ],
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format=response_format,
                )

        results = await asyncio.gather(
            *(_one(p) for p in prompts),
            return_exceptions=True,
        )
        # asyncio.gather's BaseException return type is too wide for our
        # callers; narrow to ChatResult | Exception (BaseException
        # subclasses we don't catch — KeyboardInterrupt etc — would have
        # propagated already since gather lets BaseException through).
        return [r if isinstance(r, (ChatResult, Exception)) else Exception(repr(r)) for r in results]

    async def _call_with_retry(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int | None,
        response_format: dict[str, Any] | None,
    ) -> ChatResult:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            retry=retry_if_exception_type(_RETRYABLE),
            before_sleep=before_sleep_log(log, logging.WARNING),
            reraise=True,
        ):
            with attempt:
                try:
                    resp = await self._client.chat.completions.create(
                        model=model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        response_format=response_format,
                    )
                except APIStatusError as exc:
                    log.error(
                        "OpenRouter API error (status=%s, model=%s): %s",
                        exc.status_code,
                        model,
                        exc.message,
                    )
                    raise
                return _to_chat_result(resp)
        # AsyncRetrying with reraise=True always either returns or raises;
        # the loop body is the single normal exit. Mypy can't see that.
        raise RuntimeError("unreachable: AsyncRetrying exhausted without raising")


# --- Shared helpers --------------------------------------------------------


def _to_chat_result(resp: Any) -> ChatResult:
    choice = resp.choices[0]
    usage = resp.usage
    return ChatResult(
        content=choice.message.content or "",
        model=resp.model,
        finish_reason=choice.finish_reason,
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
    )


__all__ = [
    "OpenRouterClient",
    "AsyncOpenRouterClient",
    "ChatResult",
    "Prompt",
    "OPENROUTER_BASE_URL",
]
