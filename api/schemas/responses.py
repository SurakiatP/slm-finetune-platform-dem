"""Generic response shapes used across multiple routers."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Cursor-less pagination wrapper."""

    model_config = ConfigDict(extra="forbid")

    items: list[T]
    total: int = Field(..., ge=0)
    limit: int = Field(..., gt=0, le=500)
    offset: int = Field(..., ge=0)


class ErrorResponse(BaseModel):
    """Uniform error body emitted for non-validation HTTP errors."""

    model_config = ConfigDict(extra="forbid")

    detail: str
    code: str | None = None
    extra: dict[str, Any] | None = None


__all__ = ["Page", "ErrorResponse"]
