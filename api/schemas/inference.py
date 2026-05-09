"""OpenAI-compatible inference schemas.

Trained models are served via Ollama; the API layer accepts OpenAI Chat /
Completions / Models requests and forwards them. Request schemas use
`extra="forbid"` so caller mistakes surface immediately. Response schemas
use `extra="ignore"` because they're pass-throughs — both Ollama and the
upstream OpenAI spec keep adding fields (`system_fingerprint`,
`reasoning_content`, etc.), and we'd rather drop unknown fields silently
than 500 the moment a new one ships.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

# --- Chat -------------------------------------------------------------------


class ChatMessage(BaseModel):
    # Lenient because this is also the type of the response's
    # choice.message — Ollama adds fields like `tool_calls` /
    # `reasoning_content` / `refusal` over time. The request side
    # (where we'd want strictness) gets its validation from
    # ChatCompletionRequest's own forbid on the surrounding object.
    model_config = ConfigDict(extra="ignore")

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: str | None = None
    tool_call_id: str | None = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(..., description="Ollama model tag, e.g. 'slm-platform/cls-1234:latest'.")
    messages: list[ChatMessage] = Field(..., min_length=1)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    max_tokens: int | None = Field(default=None, ge=1, le=8192)
    stream: bool = False
    stop: list[str] | None = None
    seed: int | None = None


class ChatCompletionUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionChoice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    index: int
    message: ChatMessage
    finish_reason: Literal["stop", "length", "tool_calls", "content_filter"] | None


class ChatCompletionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid4().hex[:24]}")
    object: Literal["chat.completion"] = "chat.completion"
    created: int  # unix timestamp
    model: str
    choices: list[ChatCompletionChoice]
    usage: ChatCompletionUsage


# --- Legacy text completion -------------------------------------------------


class CompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    prompt: str | list[str]
    temperature: float = 0.7
    top_p: float = 1.0
    max_tokens: int = Field(default=256, ge=1, le=8192)
    stream: bool = False
    stop: list[str] | None = None
    seed: int | None = None


class CompletionChoice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    index: int
    text: str
    finish_reason: Literal["stop", "length", "content_filter"] | None


class CompletionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: f"cmpl-{uuid4().hex[:24]}")
    object: Literal["text_completion"] = "text_completion"
    created: int
    model: str
    choices: list[CompletionChoice]
    usage: ChatCompletionUsage


# --- Models list ------------------------------------------------------------


class ModelDescriptor(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    object: Literal["model"] = "model"
    created: int
    owned_by: str = "slm-platform"
    metadata: dict[str, Any] | None = None


class ModelDescriptorList(BaseModel):
    model_config = ConfigDict(extra="ignore")

    object: Literal["list"] = "list"
    data: list[ModelDescriptor]


__all__ = [
    "ChatMessage",
    "ChatCompletionRequest",
    "ChatCompletionUsage",
    "ChatCompletionChoice",
    "ChatCompletionResponse",
    "CompletionRequest",
    "CompletionChoice",
    "CompletionResponse",
    "ModelDescriptor",
    "ModelDescriptorList",
]
