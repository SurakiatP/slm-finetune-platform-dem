"""Unit tests for holdout split (classification stratified, tool stratified, qa random)."""

from __future__ import annotations

import json
import random
from collections import Counter

import pytest

from ai_engine.data_gen.holdout_split import split_rows
from api.schemas.enums import TaskType


def _seeded_rng() -> random.Random:
    return random.Random(42)


def test_qa_random_split_sizes_exact():
    rows = [{"question": f"q{i}", "answer": f"a{i}"} for i in range(50)]
    train, holdout = split_rows(rows, TaskType.QA, holdout_size=10, rng=_seeded_rng())
    assert len(train) == 40
    assert len(holdout) == 10
    train_qs = {r["question"] for r in train}
    holdout_qs = {r["question"] for r in holdout}
    assert train_qs.isdisjoint(holdout_qs)
    assert train_qs | holdout_qs == {r["question"] for r in rows}


def test_holdout_size_zero_returns_all_as_train():
    rows = [{"question": f"q{i}", "answer": f"a{i}"} for i in range(5)]
    train, holdout = split_rows(rows, TaskType.QA, holdout_size=0, rng=_seeded_rng())
    assert len(train) == 5
    assert holdout == []


def test_holdout_size_clamped_when_larger_than_total():
    rows = [{"question": f"q{i}", "answer": f"a{i}"} for i in range(3)]
    train, holdout = split_rows(rows, TaskType.QA, holdout_size=10, rng=_seeded_rng())
    assert len(holdout) == 3
    assert train == []


def test_classification_stratified_preserves_label_proportions():
    rows = (
        [{"text": f"t{i}", "label": "A"} for i in range(60)]
        + [{"text": f"u{i}", "label": "B"} for i in range(30)]
        + [{"text": f"v{i}", "label": "C"} for i in range(10)]
    )
    train, holdout = split_rows(
        rows, TaskType.CLASSIFICATION, holdout_size=10, rng=_seeded_rng()
    )
    assert len(holdout) == 10
    assert len(train) == 90
    counts = Counter(r["label"] for r in holdout)
    assert counts["A"] in (5, 6, 7)
    assert counts["B"] in (2, 3, 4)
    assert counts["C"] in (0, 1, 2)


def test_classification_no_label_loss_in_holdout_when_possible():
    rows = [
        {"text": f"t-{lbl}-{i}", "label": lbl}
        for lbl in ["A", "B", "C", "D"]
        for i in range(5)
    ]
    train, holdout = split_rows(
        rows, TaskType.CLASSIFICATION, holdout_size=4, rng=_seeded_rng()
    )
    assert len(holdout) == 4
    labels_in_holdout = {r["label"] for r in holdout}
    assert labels_in_holdout == {"A", "B", "C", "D"}


def test_tool_calling_stratified_by_tool_name():
    rows = []
    for tool in ["play_music", "set_oven", "no_tool_needed"]:
        for i in range(10):
            rows.append({
                "question": f"q-{tool}-{i}",
                "answer": json.dumps({"name": tool, "parameters": {}}),
            })
    train, holdout = split_rows(
        rows, TaskType.TOOL_CALLING, holdout_size=6, rng=_seeded_rng()
    )
    assert len(holdout) == 6
    holdout_tools = Counter(json.loads(r["answer"])["name"] for r in holdout)
    assert all(1 <= c <= 3 for c in holdout_tools.values())
    assert set(holdout_tools.keys()) == {"play_music", "set_oven", "no_tool_needed"}


def test_tool_calling_unparseable_answer_goes_to_fallback_bucket():
    rows = [
        {"question": "q1", "answer": json.dumps({"name": "play_music", "parameters": {}})},
        {"question": "q2", "answer": "not-valid-json"},
        {"question": "q3", "answer": json.dumps({"parameters": {}})},
    ]
    train, holdout = split_rows(
        rows, TaskType.TOOL_CALLING, holdout_size=1, rng=_seeded_rng()
    )
    assert len(holdout) == 1
    assert len(train) == 2


def test_empty_input_returns_empty_pair():
    train, holdout = split_rows([], TaskType.QA, holdout_size=10, rng=_seeded_rng())
    assert train == []
    assert holdout == []


def test_deterministic_with_seeded_rng():
    rows = [{"text": f"t{i}", "label": "X"} for i in range(20)]
    train1, holdout1 = split_rows(
        rows, TaskType.CLASSIFICATION, holdout_size=5, rng=random.Random(123)
    )
    train2, holdout2 = split_rows(
        rows, TaskType.CLASSIFICATION, holdout_size=5, rng=random.Random(123)
    )
    assert [r["text"] for r in train1] == [r["text"] for r in train2]
    assert [r["text"] for r in holdout1] == [r["text"] for r in holdout2]
