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
from collections.abc import Callable
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


def is_breaker_failure(exc: BaseException) -> bool:
    """Should this exception count as one failed call against a circuit breaker?

    True for the existing `_RETRYABLE` tuple (connection/timeout/rate-limit)
    and for any 5xx `APIStatusError`. Explicitly False for every other 4xx
    (including 429 handled separately above — a `RateLimitError` is already
    covered by `_RETRYABLE`) and for anything else.

    This is evaluated only *after* tenacity's 4 attempts are exhausted, so
    one `True` here means one genuinely-failed logical call, not one flaky
    packet — the breaker counts calls, not retry attempts. Never counting a
    non-429 4xx matches the existing retry policy (those aren't retried
    either, because retrying a bad request just repeats the same client
    error) and keeps a single malformed request from tripping the breaker
    for every other caller.
    """
    if isinstance(exc, _RETRYABLE):
        return True
    if isinstance(exc, APIStatusError):
        return 500 <= exc.status_code < 600
    return False


# Hook types shared by both clients. Both are optional and default to None,
# i.e. a no-op — clients behave byte-for-byte as before when unused.
PrecheckHook = Callable[[], None]
OnCallFailureHook = Callable[[BaseException], None]
# Fires once per *logical* call that returned a result. A consumer counting
# consecutive failures needs this to reset its counter: without it a breaker
# accumulates failures over the process's whole lifetime and, once tripped,
# has nothing that can ever close it again.
OnCallSuccessHook = Callable[[], None]


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
        *,
        precheck: PrecheckHook | None = None,
        on_call_failure: OnCallFailureHook | None = None,
        on_call_success: OnCallSuccessHook | None = None,
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
        # Circuit-breaker injection points (api/services/circuit_breaker.py
        # wires these in from the worker side). This module stays stateless:
        # no counting, no Redis — just call-outs at the right moments.
        self._precheck = precheck
        self._on_call_failure = on_call_failure
        self._on_call_success = on_call_success

    @property
    def teacher_model(self) -> str:
        return self._teacher_model

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
        """One chat completion call. Retries network/timeout/rate-limit errors only.

        `precheck` (if configured) runs before every underlying SDK call
        attempt, including each retry — this is how a caller (the worker's
        circuit breaker) makes an already-open breaker fail fast instead of
        grinding through 4 attempts. `on_call_failure` (if configured) fires
        exactly once for this *logical* call — after tenacity's retries are
        exhausted, not once per attempt — and only when `is_breaker_failure`
        says the final exception should count against the breaker.
        """
        return self._run_with_failure_hook(
            model or self._teacher_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
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

        Same `precheck` / `on_call_failure` hook semantics as `chat` — see
        that docstring.
        """
        return self._run_with_failure_hook(
            model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )

    def _run_with_failure_hook(
        self,
        model: str,
        *,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int | None,
        response_format: dict[str, Any] | None,
    ) -> ChatResult:
        """Plain (undecorated) outer call: runs the retried inner attempt and
        fires exactly one terminal hook — `on_call_success` if a result came
        back, `on_call_failure` if the final exception counts per
        `is_breaker_failure`. Kept separate from `_chat_attempt` (which
        carries the `@retry` decorator) so neither hook can fire once per
        retry attempt.

        Both hooks are terminal and mutually exclusive, which is what lets a
        consumer count *consecutive* failures: a call that eventually
        succeeds after three retries reports success, not three failures.
        """
        try:
            result = self._chat_attempt(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )
        except BaseException as exc:
            if self._on_call_failure is not None and is_breaker_failure(exc):
                self._on_call_failure(exc)
            raise
        if self._on_call_success is not None:
            self._on_call_success()
        return result

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(_RETRYABLE),
        before_sleep=before_sleep_log(log, logging.WARNING),
        reraise=True,
    )
    def _chat_attempt(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int | None,
        response_format: dict[str, Any] | None,
    ) -> ChatResult:
        """Single SDK call attempt; tenacity retries this whole method body."""
        if self._precheck is not None:
            self._precheck()
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
        *,
        precheck: PrecheckHook | None = None,
        on_call_failure: OnCallFailureHook | None = None,
        on_call_success: OnCallSuccessHook | None = None,
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
        # Same circuit-breaker injection points as the sync client — see its
        # __init__ comment. Stateless here too: no counting, no Redis.
        self._precheck = precheck
        self._on_call_failure = on_call_failure
        self._on_call_success = on_call_success

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
        """Single funnel for both `chat` and `chat_batch`.

        `precheck` (if configured) runs before every SDK call attempt inside
        the `AsyncRetrying` loop below — including retries — so an
        already-open breaker fails fast instead of grinding through 4
        attempts. `on_call_failure` (if configured) fires exactly once per
        *logical* call: the `try/except` wraps the whole retry loop, not an
        individual attempt, so it only sees the final exception after
        retries are exhausted, and only calls the hook when
        `is_breaker_failure` says that exception should count.

        Note for `chat_batch` callers: an exception raised by `precheck`
        (or any other exception here) is not swallowed by `chat_batch`'s
        `return_exceptions=True` — it comes back as that prompt's exception
        instance in the results list, which is the intended fail-fast
        behaviour, not a bug.
        """
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(4),
                wait=wait_exponential(multiplier=1, min=2, max=30),
                retry=retry_if_exception_type(_RETRYABLE),
                before_sleep=before_sleep_log(log, logging.WARNING),
                reraise=True,
            ):
                with attempt:
                    if self._precheck is not None:
                        self._precheck()
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
                    result = _to_chat_result(resp)
                    break
            else:
                # AsyncRetrying with reraise=True always either breaks out of
                # the loop or raises; exhausting it normally is unreachable.
                # Mypy can't see that.
                raise RuntimeError("unreachable: AsyncRetrying exhausted without raising")
        except BaseException as exc:
            if self._on_call_failure is not None and is_breaker_failure(exc):
                self._on_call_failure(exc)
            raise
        # Deliberately outside the `try`: a hook that raises is the caller's
        # bug, and must not be misreported to `on_call_failure` as an
        # OpenRouter failure.
        if self._on_call_success is not None:
            self._on_call_success()
        return result


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
    "is_breaker_failure",
    "PrecheckHook",
    "OnCallFailureHook",
    "OnCallSuccessHook",
]
