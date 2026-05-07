"""Prompt builders for synthetic data generation.

Six combinations: 3 task types × 2 SDG modes (with_seed / description_only).
All prompts ask the teacher to emit `{"samples": [<rows>]}` so we can rely on
OpenRouter's `response_format={"type": "json_object"}` (top-level object required).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from api.schemas.data_formats import ToolDefinition
from api.schemas.enums import SDGMode, TaskType


@dataclass(frozen=True)
class Prompt:
    system: str
    user: str

    def as_messages(self) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self.system},
            {"role": "user", "content": self.user},
        ]


_BASE_SYSTEM = (
    "You are a synthetic data generation assistant for fine-tuning small "
    "language models. You produce high-quality, diverse training data in strict "
    "JSON format. You never include explanations or markdown — only the JSON object "
    'matching the schema {"samples": [...]}.'
)


# ---- Per-task system blurbs (appended to _BASE_SYSTEM) --------------------

_TASK_INSTRUCTIONS: dict[TaskType, str] = {
    TaskType.CLASSIFICATION: (
        "You are generating CLASSIFICATION training data. Each sample is "
        '{"text": "<input>", "label": "<one of the closed label set>"}.'
    ),
    TaskType.TOOL_CALLING: (
        "You are generating TOOL CALLING training data. Each sample is "
        '{"question": "<user instruction>", "answer": "<JSON STRING containing '
        '\\"name\\" and \\"parameters\\">"}. The `answer` field MUST be a string '
        "(JSON-encoded), not an object."
    ),
    TaskType.QA: (
        "You are generating QUESTION-ANSWERING training data. Each sample is "
        '{"question": "<user question>", "answer": "<free-form helpful answer>"}.'
    ),
}


# ---- Helpers --------------------------------------------------------------


def _json_block(label: str, data: Any) -> str:
    return f"{label}:\n```json\n{json.dumps(data, ensure_ascii=False, indent=2)}\n```"


def _shared_user_footer(batch_size: int) -> str:
    return (
        f"Generate exactly {batch_size} samples. Make them diverse — vary "
        "topic, phrasing, length, and edge cases. Avoid duplicating any "
        "examples shown above.\n\n"
        f'Return ONLY this JSON object (no prose, no markdown fences):\n'
        f'{{"samples": [<{batch_size} rows>]}}'
    )


# ---- Task-specific user-prompt builders -----------------------------------


def _classification_user(
    *,
    task_description: str,
    batch_size: int,
    seed_data: list[dict[str, Any]] | None,
    labels: list[str] | None,
) -> str:
    parts = [f"Task description:\n{task_description.strip()}"]
    if labels is not None:
        parts.append(_json_block("Allowed labels (closed set — never use any other label)", labels))
    if seed_data:
        parts.append(_json_block(f"Seed examples ({len(seed_data)} rows)", seed_data))
    parts.append(_shared_user_footer(batch_size))
    return "\n\n".join(parts)


def _tool_calling_user(
    *,
    task_description: str,
    batch_size: int,
    seed_data: list[dict[str, Any]] | None,
    tool_definitions: list[ToolDefinition] | None,
) -> str:
    parts = [f"Task description:\n{task_description.strip()}"]
    if tool_definitions is not None:
        tools_payload = [t.model_dump(mode="json") for t in tool_definitions]
        parts.append(
            _json_block(
                "Available tools (only invoke tools from this list; "
                "parameter names and types must match)",
                tools_payload,
            )
        )
    if seed_data:
        parts.append(_json_block(f"Seed examples ({len(seed_data)} rows)", seed_data))
    parts.append(
        "Reminder: each sample's `answer` is a STRING containing JSON, "
        'e.g. "{\\"name\\":\\"set_oven\\",\\"parameters\\":{\\"celsius\\":250}}".'
    )
    parts.append(_shared_user_footer(batch_size))
    return "\n\n".join(parts)


def _qa_user(
    *,
    task_description: str,
    batch_size: int,
    seed_data: list[dict[str, Any]] | None,
) -> str:
    parts = [f"Task description:\n{task_description.strip()}"]
    if seed_data:
        parts.append(_json_block(f"Seed examples ({len(seed_data)} rows)", seed_data))
    parts.append(_shared_user_footer(batch_size))
    return "\n\n".join(parts)


# ---- Public dispatch ------------------------------------------------------


def build_prompt(
    task_type: TaskType,
    sdg_mode: SDGMode,
    *,
    task_description: str,
    batch_size: int,
    seed_data: list[dict[str, Any]] | None = None,
    classification_labels: list[str] | None = None,
    tool_definitions: list[ToolDefinition] | None = None,
) -> Prompt:
    """Build one (system, user) pair for a single SDG batch call.

    Validation precondition: the caller (Pydantic at the API boundary) has
    already enforced mode-specific required fields, so we only need light
    consistency checks here.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")

    if sdg_mode is SDGMode.WITH_SEED and not seed_data:
        raise ValueError("with_seed mode requires seed_data")

    system = f"{_BASE_SYSTEM}\n\n{_TASK_INSTRUCTIONS[task_type]}"

    if task_type is TaskType.CLASSIFICATION:
        user = _classification_user(
            task_description=task_description,
            batch_size=batch_size,
            seed_data=seed_data,
            labels=classification_labels,
        )
    elif task_type is TaskType.TOOL_CALLING:
        user = _tool_calling_user(
            task_description=task_description,
            batch_size=batch_size,
            seed_data=seed_data,
            tool_definitions=tool_definitions,
        )
    elif task_type is TaskType.QA:
        user = _qa_user(
            task_description=task_description,
            batch_size=batch_size,
            seed_data=seed_data,
        )
    else:  # pragma: no cover — TaskType is exhaustive
        raise ValueError(f"Unsupported task_type: {task_type}")

    return Prompt(system=system, user=user)


JSON_OBJECT_RESPONSE_FORMAT = {"type": "json_object"}


__all__ = ["Prompt", "build_prompt", "JSON_OBJECT_RESPONSE_FORMAT"]
