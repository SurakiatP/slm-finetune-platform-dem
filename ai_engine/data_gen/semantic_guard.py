"""Semantic guards for seed dataset uploads (Phase 9 hardening).

Format Detection (`format_detector.py`) validates *structure* — every row
has the canonical keys with string values. It does NOT validate that the
content actually fits the declared task type. Surfaced 2026-05-16 during
the Session 22 autonomous E2E test: a user uploaded a QA file into a
classification project. Format Detection's LLM mapper agreed to rename
`question→text, answer→label` (both targets are strings) and Pydantic
validation passed (40 unique long-sentence "labels" — clearly QA content
masquerading as classification).

What this module catches and what it does NOT:
  • classification ✅ implemented here. Reject if labels look like free
    text (high cardinality + long average length).
  • tool_calling   ❌ NOT NEEDED — `ToolCallingSample.answer` already has
    a Pydantic validator that requires JSON-decoding to {name, parameters}.
    A QA-as-tool upload fails at `parse_samples()`; the row gets dropped.
  • qa             ❌ NOT APPLICABLE — there is no closed set to enforce.

Pure functions. The caller (`datasets_service._upload_jsonl_seed`) catches
the `SemanticGuardError` and re-raises as HTTPException 422.
"""

from __future__ import annotations

from typing import Any

from api.schemas.enums import TaskType


# --- Tuning -----------------------------------------------------------------

# Below this row count we skip the unique-ratio check — a 3-row test dataset
# legitimately has unique_ratio=1.0 even for real classification. The
# avg-label-length check still applies for small datasets.
_CLS_MIN_ROWS_FOR_RATIO_CHECK = 10

# A real classification task has a small closed set of labels relative to
# dataset size. The Session 22 qa-as-cls case hit ratio=1.0 (every "label"
# was a unique long sentence); 0.5 leaves room for noisy real datasets
# (e.g. 20 distinct labels in 40 rows) while catching the clearly-wrong case.
_CLS_MAX_UNIQUE_RATIO = 0.5

# Real classification labels are short tokens or phrases ("urgent",
# "ปัญหาเทคนิค", "billing_question"). Anything averaging > 30 chars is
# almost certainly free-text (QA answers run 50+ chars).
_CLS_MAX_AVG_LABEL_LEN = 30


# --- Public -----------------------------------------------------------------


class SemanticGuardError(ValueError):
    """A row set passed structural / schema validation but the content does
    not fit the declared task type."""


def assert_semantic_fit(rows: list[dict[str, Any]], task_type: TaskType) -> None:
    """Raise SemanticGuardError if rows are obviously wrong for task_type.

    Call AFTER `parse_samples(task_type, rows)` succeeds. Pure — no I/O.
    For task_type != classification this is currently a no-op (see module
    docstring for why).
    """
    if not rows:
        return
    if task_type is TaskType.CLASSIFICATION:
        _assert_classification(rows)


# --- Internal ---------------------------------------------------------------


def _assert_classification(rows: list[dict[str, Any]]) -> None:
    labels = [str(r.get("label", "")) for r in rows]
    n = len(labels)
    avg_len = sum(len(label) for label in labels) / n
    if avg_len > _CLS_MAX_AVG_LABEL_LEN:
        raise SemanticGuardError(
            f"classification labels average {avg_len:.0f} characters "
            f"(threshold: {_CLS_MAX_AVG_LABEL_LEN}). Labels should be short "
            f"category names (e.g. 'urgent', 'ปัญหาเทคนิค'), not sentences. "
            f"Did you upload a QA file by mistake? Check that 'label' is a "
            f"category, not an answer."
        )
    if n >= _CLS_MIN_ROWS_FOR_RATIO_CHECK:
        unique = len(set(labels))
        ratio = unique / n
        if ratio > _CLS_MAX_UNIQUE_RATIO:
            raise SemanticGuardError(
                f"classification expects a closed label set, but {unique}/{n} "
                f"rows have unique 'label' values (ratio={ratio:.2f}, threshold: "
                f"{_CLS_MAX_UNIQUE_RATIO}). This looks like free-text content "
                f"uploaded into a classification project. Check that 'label' "
                f"is a category, not an answer."
            )


__all__ = ["SemanticGuardError", "assert_semantic_fit"]
