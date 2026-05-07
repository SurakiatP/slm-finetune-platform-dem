"""Thin wrapper around the OpenAI SDK pointed at OpenRouter (ADR-003).

Sync client by design: this is consumed by Celery workers, which are sync.
For async API-side use (e.g. LLM judge in the FastAPI process) build a
similar wrapper with `AsyncOpenAI`.

Retries are handled here (tenacity) rather than at every call site.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError
from tenacity import (
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
            # 4xx other than 429: don't retry, surface a useful message.
            log.error(
                "OpenRouter API error (status=%s, model=%s): %s",
                exc.status_code,
                model or self._teacher_model,
                exc.message,
            )
            raise
        choice = resp.choices[0]
        usage = resp.usage
        return ChatResult(
            content=choice.message.content or "",
            model=resp.model,
            finish_reason=choice.finish_reason,
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
        )


__all__ = ["OpenRouterClient", "ChatResult", "OPENROUTER_BASE_URL"]
