"""Per-task data sample schemas — the exact shape of one row in a dataset.

These are the canonical training-row formats. They flow through:
  • seed uploads               (raw rows)
  • SDG requests               (`seed_data` field)
  • dataset preview / download endpoints
  • the data formatters that turn rows into prompts (Phase 5)

The 3 task types are locked by ADR-005.
"""

from __future__ import annotations

import json
from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from api.schemas.enums import TaskType

# --- Classification ---------------------------------------------------------


class ClassificationSample(BaseModel):
    """One classification row.

    Example:
        {"text": "I can't log into my account", "label": "technical"}
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1, description="Input text to classify.")
    label: str = Field(..., min_length=1, description="Target class label.")


# --- Tool Calling -----------------------------------------------------------


class ToolCallAnswer(BaseModel):
    """The parsed structure inside a `ToolCallingSample.answer` JSON string."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="Tool / function name to call.")
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Arguments passed to the tool, keyed by parameter name.",
    )


class ToolCallingSample(BaseModel):
    """One tool-calling row.

    `answer` is a **JSON string** (not an object) per the API contract — that
    way the row matches the format the model is trained to emit.

    Example:
        {
          "question": "Set the oven to 250°C",
          "answer": "{\\"name\\":\\"set_oven\\",\\"parameters\\":{\\"celsius\\":250}}"
        }
    """

    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., min_length=1, description="User question / instruction.")
    answer: str = Field(
        ...,
        min_length=1,
        description="JSON-encoded tool call with `name` and `parameters` keys.",
    )

    @field_validator("answer")
    @classmethod
    def _answer_must_be_valid_tool_call_json(cls, v: str) -> str:
        try:
            parsed = json.loads(v)
        except json.JSONDecodeError as exc:
            raise ValueError(f"answer must be a valid JSON string: {exc.msg}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("answer JSON must decode to an object")
        # Re-validate the parsed shape — raises ValidationError if malformed.
        ToolCallAnswer.model_validate(parsed)
        return v


class ToolParameterSpec(BaseModel):
    """JSON-Schema-lite description of one parameter of a tool."""

    model_config = ConfigDict(extra="allow")  # let users supply enum, items, etc.

    type: Literal["string", "integer", "number", "boolean", "array", "object"]
    description: str | None = None
    required: bool = False


class ToolDefinition(BaseModel):
    """Definition of a callable tool (used in `description_only` SDG)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    parameters: dict[str, ToolParameterSpec] = Field(
        default_factory=dict,
        description="Mapping of parameter name → spec.",
    )


# --- QA ---------------------------------------------------------------------


class QASample(BaseModel):
    """One question-answering row.

    Example:
        {"question": "What is the return policy?",
         "answer": "You can return items within 30 days of purchase."}
    """

    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., min_length=1)
    answer: str = Field(..., min_length=1)


# --- Type aliases / dispatch ------------------------------------------------

DataSample = Union[ClassificationSample, ToolCallingSample, QASample]
"""Discriminated externally by `TaskType`; samples don't share a tag field."""

_SAMPLE_TYPE_BY_TASK: dict[TaskType, type[BaseModel]] = {
    TaskType.CLASSIFICATION: ClassificationSample,
    TaskType.TOOL_CALLING: ToolCallingSample,
    TaskType.QA: QASample,
}


def sample_model_for(task_type: TaskType) -> type[BaseModel]:
    """Return the Pydantic model class that validates one row of the given task."""
    return _SAMPLE_TYPE_BY_TASK[task_type]


def parse_samples(task_type: TaskType, rows: list[dict[str, Any]]) -> list[BaseModel]:
    """Validate a list of raw dicts as samples of the given task type.

    Raises pydantic.ValidationError with the failing row index if any row is invalid.
    """
    model_cls = sample_model_for(task_type)
    adapter: TypeAdapter[list[BaseModel]] = TypeAdapter(list[model_cls])  # type: ignore[valid-type]
    return adapter.validate_python(rows)


__all__ = [
    "ClassificationSample",
    "ToolCallAnswer",
    "ToolCallingSample",
    "ToolParameterSpec",
    "ToolDefinition",
    "QASample",
    "DataSample",
    "sample_model_for",
    "parse_samples",
]
