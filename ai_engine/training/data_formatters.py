"""Per-task row-to-text formatters.

The fine-tuning loop turns each row of training data into a single string
the model is trained to predict. Format choice is task-dependent:

  • Classification → simple text/label tagging (require.md)
  • Tool calling   → ChatML (require.md) — system prompt + user + assistant
  • QA             → Alpaca instruction format (require.md)

These are PURE functions: no torch/HF imports, importable on any host.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from api.schemas.data_formats import ToolDefinition
from api.schemas.enums import TaskType

# ---- Templates -------------------------------------------------------------

CLASSIFICATION_TEMPLATE = "### Text: {text}\n### Label: {label}"

ALPACA_TEMPLATE = (
    "Below is an instruction that describes a task. Write a response that "
    "appropriately completes the request.\n\n"
    "### Instruction:\n{question}\n\n"
    "### Response:\n{answer}"
)

CHATML_SYSTEM_GENERIC = (
    "You are a helpful assistant. Respond ONLY with a JSON object containing "
    "the keys 'name' (the tool to call) and 'parameters' (the arguments)."
)

CHATML_SYSTEM_WITH_TOOLS = (
    "You are a helpful assistant. You have access to the following tools "
    "and must respond ONLY with a JSON object containing the keys 'name' "
    "and 'parameters'.\n\nAvailable tools:\n{tools}"
)

CHATML_TEMPLATE = (
    "<|im_start|>system\n{system}<|im_end|>\n"
    "<|im_start|>user\n{question}<|im_end|>\n"
    "<|im_start|>assistant\n{answer}<|im_end|>"
)


# ---- Formatters ------------------------------------------------------------


def format_classification(row: dict[str, Any]) -> str:
    return CLASSIFICATION_TEMPLATE.format(text=row["text"], label=row["label"])


def format_qa(row: dict[str, Any]) -> str:
    return ALPACA_TEMPLATE.format(question=row["question"], answer=row["answer"])


def format_tool_calling(
    row: dict[str, Any],
    *,
    tool_definitions: list[ToolDefinition] | None = None,
) -> str:
    if tool_definitions:
        tools_text = json.dumps(
            [t.model_dump(mode="json") for t in tool_definitions],
            ensure_ascii=False,
            indent=2,
        )
        system = CHATML_SYSTEM_WITH_TOOLS.format(tools=tools_text)
    else:
        system = CHATML_SYSTEM_GENERIC
    return CHATML_TEMPLATE.format(
        system=system,
        question=row["question"],
        answer=row["answer"],
    )


# ---- Dispatch --------------------------------------------------------------


def get_formatter(
    task_type: TaskType,
    *,
    tool_definitions: list[ToolDefinition] | None = None,
) -> Callable[[dict[str, Any]], str]:
    """Return `(row) -> str` for the given task type."""
    if task_type is TaskType.CLASSIFICATION:
        return format_classification
    if task_type is TaskType.QA:
        return format_qa
    if task_type is TaskType.TOOL_CALLING:
        # Bind tool_definitions once.
        tools = list(tool_definitions) if tool_definitions else None
        return lambda row: format_tool_calling(row, tool_definitions=tools)
    raise ValueError(f"Unsupported task_type: {task_type}")  # pragma: no cover


__all__ = [
    "CLASSIFICATION_TEMPLATE",
    "ALPACA_TEMPLATE",
    "CHATML_SYSTEM_GENERIC",
    "CHATML_SYSTEM_WITH_TOOLS",
    "CHATML_TEMPLATE",
    "format_classification",
    "format_qa",
    "format_tool_calling",
    "get_formatter",
]
