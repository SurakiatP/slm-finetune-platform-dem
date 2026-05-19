"""Tier-1 characterization snapshots for pure helpers in `generator.py`.

Targets module-level utility functions inside `ai_engine/data_gen/generator.py`
that are pure (no IO, no state) and used during SDG setup before the main
generation loop. Class methods like `_build_batch_inputs` need a full
`SyntheticDataGenerator` instance and belong to the Tier-2 (mocked-external)
suite — they're left out of this Tier-1 file on purpose.
"""

from __future__ import annotations

import pytest

from ai_engine.data_gen.generator import (
    _compute_classification_quota,
    _compute_tool_quota,
    _group_by,
    _group_tool_examples,
    _make_sentinel_tool_def,
    _seed_text_field,
)
from api.schemas.enums import TaskType


# ---- _compute_classification_quota ------------------------------------------


@pytest.mark.parametrize(
    "labels,target",
    [
        (["a", "b", "c", "unknown"], 100),  # standard 3-class + sentinel, round number
        (["a", "b", "c", "unknown"], 47),   # odd target — ceil rounding visible
        (["a", "unknown"], 10),             # minimum real-class count
        (["a", "b"], 50),                   # no sentinel — even split fallback
        (["unknown"], 20),                  # only sentinel
    ],
    ids=["100_4cls", "47_4cls_uneven", "10_2cls", "50_no_sentinel", "20_sentinel_only"],
)
def test_compute_classification_quota(snapshot, labels: list[str], target: int):
    assert _compute_classification_quota(labels, target) == snapshot


# ---- _compute_tool_quota ----------------------------------------------------


@pytest.mark.parametrize(
    "tools,target",
    [
        (["set_volume", "play_music", "light_on", "no_tool_needed"], 100),
        (["set_volume", "play_music", "no_tool_needed"], 33),
        (["set_volume", "no_tool_needed"], 11),
        (["set_volume", "play_music"], 50),   # no sentinel — even split
        (["no_tool_needed"], 30),              # only sentinel
    ],
    ids=["100_4tools", "33_3tools", "11_2tools", "50_no_sentinel", "30_sentinel_only"],
)
def test_compute_tool_quota(snapshot, tools: list[str], target: int):
    assert _compute_tool_quota(tools, target) == snapshot


# ---- _group_by --------------------------------------------------------------


def test_group_by_classification_rows(snapshot):
    rows = [
        {"text": "wifi เน่า", "label": "ปัญหาเทคนิค"},
        {"text": "ขอคืนเงิน", "label": "ปัญหาการเงิน"},
        {"text": "vpn ใช้ไม่ได้", "label": "ปัญหาเทคนิค"},
        {"text": "เปลี่ยน address", "label": "คำถามทั่วไป"},
    ]
    assert _group_by(rows, key="label") == snapshot


def test_group_by_drops_blank_keys(snapshot):
    rows = [
        {"text": "a", "label": ""},
        {"text": "b", "label": "  "},
        {"text": "c", "label": "real"},
    ]
    assert _group_by(rows, key="label") == snapshot


# ---- _group_tool_examples ---------------------------------------------------


def test_group_tool_examples_normal(snapshot):
    rows = [
        {"question": "เพิ่มเสียง", "answer": '{"name":"set_volume","parameters":{"level":80}}'},
        {"question": "เปิดเพลง", "answer": '{"name":"play_music","parameters":{"track":"X"}}'},
        {"question": "ลดเสียงหน่อย", "answer": '{"name":"set_volume","parameters":{"level":20}}'},
    ]
    assert _group_tool_examples(rows) == snapshot


def test_group_tool_examples_skips_bad_json(snapshot):
    rows = [
        {"question": "ok", "answer": '{"name":"set_volume","parameters":{}}'},
        {"question": "bad", "answer": "this is not json"},
        {"question": "missing_name", "answer": '{"parameters":{}}'},
    ]
    assert _group_tool_examples(rows) == snapshot


# ---- _make_sentinel_tool_def ------------------------------------------------


def test_make_sentinel_tool_def(snapshot):
    td = _make_sentinel_tool_def()
    assert td.model_dump(mode="json") == snapshot


# ---- _seed_text_field -------------------------------------------------------


@pytest.mark.parametrize(
    "task_type,expected",
    [
        (TaskType.CLASSIFICATION, "text"),
        (TaskType.QA, "question"),
        (TaskType.TOOL_CALLING, "question"),
    ],
)
def test_seed_text_field(task_type: TaskType, expected: str):
    assert _seed_text_field(task_type) == expected
