"""Per-task row-to-messages formatters.

The fine-tuning loop turns each row of training data into a list of chat
messages (`[{"role": ..., "content": ...}, ...]`). Unsloth's
`get_chat_template()` then renders them to the base model's native chat
template at SFTTrainer time, so EOS tokens etc. are picked up correctly.

  - Classification → user: text → assistant: label
  - QA             → user: question → assistant: answer
  - Tool calling   → optional system prompt with tool definitions
                     → user: question → assistant: JSON tool call

These are PURE functions: no torch/HF imports, importable on any host.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from api.schemas.data_formats import ToolDefinition
from api.schemas.enums import TaskType

# ---- System prompts (tool calling) -----------------------------------------

CHATML_SYSTEM_GENERIC = (
    "You are a helpful assistant. Respond ONLY with a JSON object containing "
    "the keys 'name' (the tool to call) and 'parameters' (the arguments)."
)

CHATML_SYSTEM_WITH_TOOLS = (
    "You are a helpful assistant. You have access to the following tools "
    "and must respond ONLY with a JSON object containing the keys 'name' "
    "and 'parameters'.\n\nAvailable tools:\n{tools}"
)


# ---- Formatters ------------------------------------------------------------

Message = dict[str, str]


def format_classification(row: dict[str, Any]) -> list[Message]:
    return [
        {"role": "user", "content": str(row["text"])},
        {"role": "assistant", "content": str(row["label"])},
    ]


def format_qa(row: dict[str, Any]) -> list[Message]:
    return [
        {"role": "user", "content": str(row["question"])},
        {"role": "assistant", "content": str(row["answer"])},
    ]


def format_tool_calling(
    row: dict[str, Any],
    *,
    tool_definitions: list[ToolDefinition] | None = None,
) -> list[Message]:
    if tool_definitions:
        tools_text = json.dumps(
            [t.model_dump(mode="json") for t in tool_definitions],
            ensure_ascii=False,
            indent=2,
        )
        system = CHATML_SYSTEM_WITH_TOOLS.format(tools=tools_text)
    else:
        system = CHATML_SYSTEM_GENERIC
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": str(row["question"])},
        {"role": "assistant", "content": str(row["answer"])},
    ]


# ---- Dispatch --------------------------------------------------------------


def get_formatter(
    task_type: TaskType,
    *,
    tool_definitions: list[ToolDefinition] | None = None,
) -> Callable[[dict[str, Any]], list[Message]]:
    """Return `(row) -> list[{role, content}]` for the given task type."""
    if task_type is TaskType.CLASSIFICATION:
        return format_classification
    if task_type is TaskType.QA:
        return format_qa
    if task_type is TaskType.TOOL_CALLING:
        tools = list(tool_definitions) if tool_definitions else None
        return lambda row: format_tool_calling(row, tool_definitions=tools)
    raise ValueError(f"Unsupported task_type: {task_type}")  # pragma: no cover


__all__ = [
    "CHATML_SYSTEM_GENERIC",
    "CHATML_SYSTEM_WITH_TOOLS",
    "Message",
    "format_classification",
    "format_qa",
    "format_tool_calling",
    "get_formatter",
]
