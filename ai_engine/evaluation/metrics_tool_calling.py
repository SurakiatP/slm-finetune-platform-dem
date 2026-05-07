"""Tool-calling metrics — JSON validity, name accuracy, arg accuracy.

The model output for tool-calling is a JSON string with `name` + `parameters`
keys. We grade three things:

  1. **JSON validity** — does the string parse + have the right shape.
  2. **Name accuracy** — does `name` match the expected tool.
  3. **Arg accuracy** — for matching-name rows, does `parameters` equal the
     expected dict (key set + per-value equality, lenient int/float).

Pure domain. No imports beyond stdlib.
"""

from __future__ import annotations

import json
from typing import Any


def compute_metrics(
    *,
    predicted: list[str],
    expected: list[str],
) -> dict[str, Any]:
    """Compute tool-calling metrics row-by-row.

    Args:
        predicted: model outputs — JSON-encoded strings (the `answer` field).
        expected: gold answers in the same JSON-encoded-string form.

    Returns:
        ``{"json_validity", "name_accuracy", "arg_accuracy", "exact_match",
            "n"}`` — all four ratios in [0,1].
    """
    if len(predicted) != len(expected):
        raise ValueError(
            f"predicted/expected length mismatch: {len(predicted)} vs {len(expected)}"
        )
    if not predicted:
        raise ValueError("compute_metrics: empty inputs")

    n = len(predicted)
    n_valid = 0
    n_name_correct = 0
    n_args_correct = 0
    n_exact = 0
    n_eligible_for_args = 0  # rows where both pred and expected parsed and names match

    for pred_str, exp_str in zip(predicted, expected):
        pred = _safe_parse(pred_str)
        exp = _safe_parse(exp_str)

        if exp is None:
            # Test data is malformed — skip from name/arg/exact tallies but
            # the row still penalises overall validity.
            continue

        if pred is None:
            continue
        n_valid += 1

        if pred.get("name") == exp.get("name"):
            n_name_correct += 1
            n_eligible_for_args += 1
            if _params_equal(pred.get("parameters"), exp.get("parameters")):
                n_args_correct += 1
                n_exact += 1
        # else: counts as a name miss; not arg-eligible.

    return {
        "json_validity": n_valid / n,
        "name_accuracy": n_name_correct / n,
        # arg_accuracy is conditional on name match — division by 0 yields 0.0
        "arg_accuracy": (n_args_correct / n_eligible_for_args) if n_eligible_for_args else 0.0,
        "exact_match": n_exact / n,
        "n": n,
    }


# ---- helpers ---------------------------------------------------------------


def _safe_parse(raw: str) -> dict[str, Any] | None:
    """Return a dict if `raw` parses to one with `name` + `parameters` keys."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if text.startswith("```"):
        # Tolerate fenced blocks emitted by chatty models.
        text = text.lstrip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.split("```", 1)[0].strip()
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    if "name" not in obj or "parameters" not in obj:
        return None
    if not isinstance(obj["name"], str):
        return None
    if not isinstance(obj["parameters"], dict):
        return None
    return obj


def _params_equal(a: Any, b: Any) -> bool:
    """Lenient deep equality for tool params.

    Treats bools as distinct from ints (so `True != 1`); allows int/float
    cross-comparison if numerically equal. Unknown types fall back to `==`.
    """
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            return False
        return all(_params_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return False
        return all(_params_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    return a == b


__all__ = ["compute_metrics"]
