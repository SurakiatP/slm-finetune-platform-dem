"""Per-task validation of teacher-generated rows.

Two-stage validation:
  1. **Schema** — Pydantic validation against the task's sample model
     (`ClassificationSample` / `ToolCallingSample` / `QASample`).
     This already covers the JSON shape and the JSON-string `answer`
     format for tool-calling.
  2. **Business rules** — closed label set for classification; tool name
     and parameter type/required checks for tool-calling. QA has none.

Per-row failures are returned alongside accepted rows (we never raise on a
single bad row — keep the good ones and let the caller log/throttle).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from api.schemas.data_formats import ToolDefinition, sample_model_for
from api.schemas.enums import TaskType


@dataclass(frozen=True)
class ValidationFailure:
    """One rejected row, with the original index and a human-readable reason."""

    index: int
    reason: str


# ---- Tool-calling helpers --------------------------------------------------


_PRIMITIVE_TYPECHECK: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _type_matches(value: Any, expected: str) -> bool:
    """Lenient JSON Schema-style type check (booleans aren't ints/numbers)."""
    if expected == "boolean":
        return isinstance(value, bool)
    if expected in ("integer", "number") and isinstance(value, bool):
        return False  # JSON Schema treats booleans as not-numbers
    expected_type = _PRIMITIVE_TYPECHECK.get(expected)
    if expected_type is None:
        return True  # unknown spec type — be permissive
    return isinstance(value, expected_type)


def _check_tool_call(answer_json: str, tools_by_name: dict[str, ToolDefinition]) -> str | None:
    """Return None if the tool call is well-formed, else an error message.

    Pre-condition: `answer_json` was already accepted by Pydantic (so the
    JSON parses and contains `name` + `parameters`). This re-parses to apply
    business rules.
    """
    try:
        obj = json.loads(answer_json)
    except json.JSONDecodeError as exc:  # pragma: no cover — Pydantic catches first
        return f"answer is not valid JSON: {exc.msg}"
    name = obj["name"]
    params = obj["parameters"]
    if name not in tools_by_name:
        allowed = sorted(tools_by_name)
        return f"unknown tool '{name}' (allowed: {allowed})"

    spec = tools_by_name[name]
    if not isinstance(params, dict):  # pragma: no cover — Pydantic catches
        return "parameters must be an object"

    # Required parameters present?
    for pname, pspec in spec.parameters.items():
        if pspec.required and pname not in params:
            return f"required parameter '{pname}' missing from parameters"
    # No unknown parameters? (Permissive — extra params often appear in real data.)
    # Type checks for known parameters.
    for pname, pvalue in params.items():
        pspec = spec.parameters.get(pname)
        if pspec is None:
            continue
        if not _type_matches(pvalue, pspec.type):
            return (
                f"parameter '{pname}' has wrong type "
                f"(expected {pspec.type}, got {type(pvalue).__name__})"
            )
    return None


# ---- Public API -----------------------------------------------------------


def validate_generated_rows(
    task_type: TaskType,
    rows: list[Any],
    *,
    classification_labels: list[str] | None = None,
    tool_definitions: list[ToolDefinition] | None = None,
) -> tuple[list[dict[str, Any]], list[ValidationFailure]]:
    """Run schema + business-rule validation on a batch of teacher-generated rows.

    Args:
        task_type: which sample shape to enforce.
        rows: raw teacher output (list of dicts; non-dicts are rejected).
        classification_labels: closed label set; if provided, rejects unknown labels.
        tool_definitions: tools for tool-calling; if provided, validates `answer`'s
            tool name + parameter signature.

    Returns:
        (accepted_canonical_rows, failures). Accepted rows are the dicts produced
        by `sample.model_dump()` so they're guaranteed to match the schema exactly.
    """
    sample_cls = sample_model_for(task_type)
    label_set: set[str] | None = (
        set(classification_labels) if classification_labels is not None else None
    )
    tools_by_name: dict[str, ToolDefinition] = {t.name: t for t in (tool_definitions or [])}

    accepted: list[dict[str, Any]] = []
    failures: list[ValidationFailure] = []

    for i, raw in enumerate(rows):
        if not isinstance(raw, dict):
            failures.append(
                ValidationFailure(i, f"row is not a JSON object (got {type(raw).__name__})")
            )
            continue

        try:
            sample = sample_cls.model_validate(raw)
        except ValidationError as exc:
            first_err = exc.errors()[0]
            loc = ".".join(str(part) for part in first_err.get("loc", ()))
            failures.append(
                ValidationFailure(i, f"schema: {loc}: {first_err.get('msg', 'invalid')}")
            )
            continue

        # Per-task business rules
        if task_type is TaskType.CLASSIFICATION and label_set is not None:
            label = sample.label  # type: ignore[attr-defined]
            if label not in label_set:
                failures.append(
                    ValidationFailure(i, f"label '{label}' not in allowed label set")
                )
                continue
        elif task_type is TaskType.TOOL_CALLING and tools_by_name:
            answer = sample.answer  # type: ignore[attr-defined]
            err = _check_tool_call(answer, tools_by_name)
            if err is not None:
                failures.append(ValidationFailure(i, err))
                continue

        accepted.append(sample.model_dump())

    return accepted, failures


__all__ = ["ValidationFailure", "validate_generated_rows"]
