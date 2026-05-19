"""Tier-1 characterization snapshots for `ai_engine/evaluation/metrics_*.py`.

Each `compute_metrics()` function is a pure mapping
`(predicted, expected) → dict[str, float|dict]`. Snapshotting fixed input
arrays freezes the exact metric values across refactors of the metric code.

The three metric modules wrap different third-party libraries
(scikit-learn / rouge-score+sacrebleu / stdlib) — keeping their snapshots
together documents the cross-task contract surface the eval worker depends on.
"""

from __future__ import annotations

import pytest

from ai_engine.evaluation import (
    metrics_classification,
    metrics_qa,
    metrics_tool_calling,
)


# ---- Classification --------------------------------------------------------


_CLS_LABELS = ["ปัญหาเทคนิค", "ปัญหาการเงิน", "คำถามทั่วไป"]


def test_metrics_classification_perfect(snapshot):
    expected = ["ปัญหาเทคนิค", "ปัญหาการเงิน", "คำถามทั่วไป", "ปัญหาเทคนิค"]
    predicted = list(expected)
    out = metrics_classification.compute_metrics(
        predicted=predicted, expected=expected, labels=_CLS_LABELS
    )
    assert out == snapshot


def test_metrics_classification_partial(snapshot):
    expected = ["ปัญหาเทคนิค", "ปัญหาการเงิน", "คำถามทั่วไป", "ปัญหาเทคนิค", "คำถามทั่วไป"]
    predicted = ["ปัญหาเทคนิค", "ปัญหาเทคนิค", "คำถามทั่วไป", "ปัญหาเทคนิค", "ปัญหาการเงิน"]
    out = metrics_classification.compute_metrics(
        predicted=predicted, expected=expected, labels=_CLS_LABELS
    )
    assert out == snapshot


def test_metrics_classification_with_out_of_set(snapshot):
    expected = ["ปัญหาเทคนิค", "ปัญหาการเงิน", "คำถามทั่วไป"]
    predicted = ["wrong_label", "ปัญหาการเงิน", "คำถามทั่วไป"]
    out = metrics_classification.compute_metrics(
        predicted=predicted, expected=expected, labels=_CLS_LABELS
    )
    assert out == snapshot


def test_metrics_classification_all_wrong(snapshot):
    expected = ["ปัญหาเทคนิค", "ปัญหาการเงิน", "คำถามทั่วไป"]
    predicted = ["คำถามทั่วไป", "ปัญหาเทคนิค", "ปัญหาการเงิน"]
    out = metrics_classification.compute_metrics(
        predicted=predicted, expected=expected, labels=_CLS_LABELS
    )
    assert out == snapshot


# ---- QA --------------------------------------------------------------------


def test_metrics_qa_perfect_match(snapshot):
    expected = [
        "ภายใน 7 วันหลังจากได้รับสินค้า",
        "โทรหา 02-123-4567 ตลอด 24 ชั่วโมง",
        "Paris.",
    ]
    predicted = list(expected)
    out = metrics_qa.compute_metrics(predicted=predicted, expected=expected)
    assert out == snapshot


def test_metrics_qa_paraphrased(snapshot):
    expected = [
        "ภายใน 7 วันหลังจากได้รับสินค้า",
        "The capital of France is Paris.",
        "There are 24 hours in a day.",
    ]
    predicted = [
        "ภายใน 7 วัน หลังได้รับ",
        "Paris is the capital of France.",
        "A day has 24 hours total.",
    ]
    out = metrics_qa.compute_metrics(predicted=predicted, expected=expected)
    assert out == snapshot


def test_metrics_qa_all_wrong(snapshot):
    expected = ["Paris.", "Bangkok.", "Tokyo."]
    predicted = ["Berlin.", "Madrid.", "Seoul."]
    out = metrics_qa.compute_metrics(predicted=predicted, expected=expected)
    assert out == snapshot


# ---- Tool calling ----------------------------------------------------------


def test_metrics_tool_perfect(snapshot):
    expected = [
        '{"name":"set_volume","parameters":{"level":80}}',
        '{"name":"play_music","parameters":{"track":"Shape of You"}}',
    ]
    predicted = list(expected)
    out = metrics_tool_calling.compute_metrics(predicted=predicted, expected=expected)
    assert out == snapshot


def test_metrics_tool_name_correct_args_wrong(snapshot):
    expected = ['{"name":"set_volume","parameters":{"level":80}}']
    predicted = ['{"name":"set_volume","parameters":{"level":50}}']
    out = metrics_tool_calling.compute_metrics(predicted=predicted, expected=expected)
    assert out == snapshot


def test_metrics_tool_invalid_json(snapshot):
    expected = ['{"name":"set_volume","parameters":{"level":80}}']
    predicted = ["this is not JSON"]
    out = metrics_tool_calling.compute_metrics(predicted=predicted, expected=expected)
    assert out == snapshot


def test_metrics_tool_mixed(snapshot):
    expected = [
        '{"name":"set_volume","parameters":{"level":80}}',
        '{"name":"play_music","parameters":{"track":"X"}}',
        '{"name":"light_on","parameters":{"room":"bedroom"}}',
        '{"name":"no_tool_needed","parameters":{}}',
    ]
    predicted = [
        '{"name":"set_volume","parameters":{"level":80}}',   # exact
        '{"name":"play_music","parameters":{"track":"Y"}}',  # name ok, args wrong
        '{"name":"set_volume","parameters":{}}',             # wrong name
        "garbage",                                            # invalid json
    ]
    out = metrics_tool_calling.compute_metrics(predicted=predicted, expected=expected)
    assert out == snapshot


# ---- Error paths (no snapshot — assert exception type) --------------------


def test_metrics_qa_empty_inputs_raise():
    with pytest.raises(ValueError):
        metrics_qa.compute_metrics(predicted=[], expected=[])


def test_metrics_classification_length_mismatch_raises():
    with pytest.raises(ValueError):
        metrics_classification.compute_metrics(
            predicted=["a"], expected=["a", "b"], labels=["a", "b"]
        )


def test_metrics_tool_empty_raises():
    with pytest.raises(ValueError):
        metrics_tool_calling.compute_metrics(predicted=[], expected=[])
