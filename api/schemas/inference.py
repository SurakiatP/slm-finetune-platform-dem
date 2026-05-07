"""OpenAI-compatible inference schemas.

Trained models are served via Ollama; the API layer accepts OpenAI Chat /
Completions / Models requests and forwards them. Only the fields the platform
actually needs are typed — extra fields are forbidden so the contract is clear.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

# --- Chat -------------------------------------------------------------------


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    message: ChatMessage
    finish_reason: Literal["stop", "length", "tool_calls", "content_filter"] | None


class ChatCompletionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    model_config = ConfigDict(extra="forbid")

    index: int
    text: str
    finish_reason: Literal["stop", "length", "content_filter"] | None


class CompletionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: f"cmpl-{uuid4().hex[:24]}")
    object: Literal["text_completion"] = "text_completion"
    created: int
    model: str
    choices: list[CompletionChoice]
    usage: ChatCompletionUsage


# --- Models list ------------------------------------------------------------


class ModelDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    object: Literal["model"] = "model"
    created: int
    owned_by: str = "slm-platform"
    metadata: dict[str, Any] | None = None


class ModelDescriptorList(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
