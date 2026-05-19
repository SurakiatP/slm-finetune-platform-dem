"""Unit tests for ai_engine/data_gen/semantic_guard.py.

These cover the Session 22 Finding #1 case: a QA file uploaded into a
classification project by mistake. Format Detection happily renames
question/answer→text/label and Pydantic accepts the result, but the
content is clearly not closed-set classification.
"""

from __future__ import annotations

import pytest

from ai_engine.data_gen.semantic_guard import (
    SemanticGuardError,
    assert_semantic_fit,
)
from api.schemas.enums import TaskType


# --- Classification: passes -------------------------------------------------


def test_classification_canonical_thai_support_passes():
    """Mirrors `seed_data/classification/classification_canonical.jsonl`:
    40 rows, 3 short distinct labels."""
    rows = [
        {"text": f"row {i}", "label": "ปัญหาการเงิน" if i % 3 == 0 else "ปัญหาเทคนิค"}
        for i in range(40)
    ]
    assert_semantic_fit(rows, TaskType.CLASSIFICATION)


def test_classification_tiny_test_dataset_passes():
    """Below the ratio-check minimum (10 rows) the unique_ratio is skipped —
    a 2-row test dataset legitimately has ratio=1.0."""
    rows = [{"text": "a", "label": "x"}, {"text": "b", "label": "y"}]
    assert_semantic_fit(rows, TaskType.CLASSIFICATION)


def test_classification_at_ratio_threshold_passes():
    """20 unique / 40 = 0.5 — exactly at threshold, must NOT raise."""
    rows = [{"text": f"row {i}", "label": f"label_{i % 20}"} for i in range(40)]
    assert_semantic_fit(rows, TaskType.CLASSIFICATION)


# --- Classification: rejects ------------------------------------------------


def test_classification_session22_qa_as_cls_case_rejected():
    """The exact Session 22 bug: 40 unique long-sentence "labels"."""
    rows = [
        {
            "text": f"row {i}",
            "label": (
                "ลูกค้าสามารถคืนสินค้าได้ภายใน 30 วันนับจากวันที่ซื้อ "
                "โดยสินค้าต้องอยู่ในสภาพสมบูรณ์ครับ"
            ),
        }
        for i in range(40)
    ]
    with pytest.raises(SemanticGuardError, match="average"):
        assert_semantic_fit(rows, TaskType.CLASSIFICATION)


def test_classification_long_labels_rejected_even_with_low_cardinality():
    """A repeated long sentence as label still fails the avg-length check —
    catches the case where Format Detection picked a constant footer field."""
    rows = [
        {"text": f"row {i}", "label": "a" * 50}
        for i in range(15)
    ]
    with pytest.raises(SemanticGuardError, match="average"):
        assert_semantic_fit(rows, TaskType.CLASSIFICATION)


def test_classification_high_cardinality_short_labels_rejected():
    """All-unique short labels still fail the ratio check — likely an ID
    column was misidentified as the label."""
    rows = [
        {"text": f"row {i}", "label": f"id_{i:03d}"}
        for i in range(100)
    ]
    with pytest.raises(SemanticGuardError, match="unique"):
        assert_semantic_fit(rows, TaskType.CLASSIFICATION)


def test_classification_short_labels_check_runs_before_ratio_check():
    """avg-length is checked first; we want the more specific QA-as-cls
    error message to surface for the Session 22 scenario."""
    rows = [
        {"text": f"row {i}", "label": "x" * 100}
        for i in range(20)
    ]
    with pytest.raises(SemanticGuardError, match="average"):
        assert_semantic_fit(rows, TaskType.CLASSIFICATION)


# --- Tool calling: no-op ----------------------------------------------------


def test_tool_calling_is_noop_validation_handled_by_pydantic():
    """tool_calling is enforced upstream by ToolCallingSample.answer's
    Pydantic validator (must JSON-decode to {name, parameters}). This guard
    must not duplicate that check or it would double-fail clean rows."""
    rows = [{"question": "Q", "answer": "not json"}] * 10
    assert_semantic_fit(rows, TaskType.TOOL_CALLING)


# --- QA: no-op --------------------------------------------------------------


def test_qa_is_noop_no_closed_set_to_enforce():
    rows = [
        {"question": "What is X?", "answer": "Long free-form answer."},
    ] * 10
    assert_semantic_fit(rows, TaskType.QA)


# --- Empty ------------------------------------------------------------------


def test_empty_rows_is_noop():
    """Upstream `if not valid: raise 400` catches this case; guard must
    return silently (don't divide by zero)."""
    assert_semantic_fit([], TaskType.CLASSIFICATION)
    assert_semantic_fit([], TaskType.QA)
    assert_semantic_fit([], TaskType.TOOL_CALLING)
