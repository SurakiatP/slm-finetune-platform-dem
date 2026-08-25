"""Tier-1 characterization snapshots for `ai_engine/data_gen/prompts.py`.

These tests treat every prompt builder as a pure function and capture its
exact output as a syrupy snapshot. They guard refactors of the SDG pipeline:
if a future change accidentally alters a system prompt or output instruction,
the diff surfaces in the test run.

Update snapshots intentionally with `pytest --snapshot-update` and explain
the diff in the commit message / PR body.
"""

from __future__ import annotations

import pytest

from ai_engine.data_gen.prompts import (
    build_generator_prompt,
    build_judge_prompt,
    build_meta_prompt,
    build_pdf_qa_messages,
    parse_generator_response,
)
from api.schemas.data_formats import ToolDefinition, ToolParameterSpec
from api.schemas.enums import TaskType


# ---- Deterministic inputs --------------------------------------------------

_CLS_LABELS = ["ปัญหาเทคนิค", "ปัญหาการเงิน", "คำถามทั่วไป", "unknown"]
_CLS_EXAMPLES = [
    {"text": "Wi-Fi กระตุก ใช้งานไม่ได้", "label": "ปัญหาเทคนิค"},
    {"text": "อยากขอคืนเงิน", "label": "ปัญหาการเงิน"},
]
_QA_EXAMPLES = [
    {"question": "นโยบายคืนสินค้ามีกี่วัน?", "answer": "ภายใน 7 วันหลังได้รับ"},
]
_TOOL_DEFS = [
    ToolDefinition(
        name="set_volume",
        description="Adjust speaker volume.",
        parameters={
            "level": ToolParameterSpec(type="integer", description="0-100"),
        },
    ),
    ToolDefinition(
        name="no_tool_needed",
        description="Sentinel — no tool applies.",
        parameters={},
    ),
]
_TOOL_EXAMPLES = [
    {
        "question": "เปิดเสียงดังหน่อย",
        "answer": '{"name":"set_volume","parameters":{"level":80}}',
    },
]
_DIVERSITY_RULE = "ใช้ภาษาเป็นทางการ เน้นถามแบบยาว"
_DIFFICULTY = "medium"
_TASK_DESC_CLS = "ระบบจัดประเภทคำร้องขอของลูกค้า support"
_TASK_DESC_QA = "ระบบตอบคำถามนโยบายการคืนสินค้า"
_TASK_DESC_TOOL = "ระบบ smart home สำหรับสั่งงานด้วยภาษาธรรมชาติ"


# ---- build_generator_prompt -------------------------------------------------


def test_generator_prompt_classification_normal(snapshot):
    prompt = build_generator_prompt(
        TaskType.CLASSIFICATION,
        task_description=_TASK_DESC_CLS,
        label_or_tool="ปัญหาเทคนิค",
        examples=_CLS_EXAMPLES,
        diversity_rule=_DIVERSITY_RULE,
        difficulty=_DIFFICULTY,
        classification_labels=_CLS_LABELS,
    )
    assert (prompt.system, prompt.user) == snapshot


def test_generator_prompt_classification_sentinel(snapshot):
    prompt = build_generator_prompt(
        TaskType.CLASSIFICATION,
        task_description=_TASK_DESC_CLS,
        label_or_tool="unknown",
        examples=[],
        diversity_rule="off-topic small talk",
        difficulty="easy",
        classification_labels=_CLS_LABELS,
        is_sentinel=True,
    )
    assert (prompt.system, prompt.user) == snapshot


def test_generator_prompt_qa(snapshot):
    prompt = build_generator_prompt(
        TaskType.QA,
        task_description=_TASK_DESC_QA,
        label_or_tool=None,
        examples=_QA_EXAMPLES,
        diversity_rule=_DIVERSITY_RULE,
        difficulty="hard",
    )
    assert (prompt.system, prompt.user) == snapshot


def test_generator_prompt_tool_calling_normal(snapshot):
    prompt = build_generator_prompt(
        TaskType.TOOL_CALLING,
        task_description=_TASK_DESC_TOOL,
        label_or_tool="set_volume",
        examples=_TOOL_EXAMPLES,
        diversity_rule=_DIVERSITY_RULE,
        difficulty=_DIFFICULTY,
        tool_definitions=_TOOL_DEFS,
    )
    assert (prompt.system, prompt.user) == snapshot


def test_generator_prompt_tool_calling_sentinel(snapshot):
    prompt = build_generator_prompt(
        TaskType.TOOL_CALLING,
        task_description=_TASK_DESC_TOOL,
        label_or_tool="no_tool_needed",
        examples=[],
        diversity_rule="off-topic small talk",
        difficulty="easy",
        tool_definitions=_TOOL_DEFS,
        is_sentinel=True,
    )
    assert (prompt.system, prompt.user) == snapshot


# ---- build_judge_prompt -----------------------------------------------------


def test_judge_prompt_classification(snapshot):
    prompt = build_judge_prompt(
        TaskType.CLASSIFICATION,
        task_description=_TASK_DESC_CLS,
        rows=[{"text": "อยากขอคืนเงิน", "label": "ปัญหาการเงิน"}],
        classification_labels=_CLS_LABELS,
    )
    assert (prompt.system, prompt.user) == snapshot


def test_judge_prompt_classification_sentinel(snapshot):
    prompt = build_judge_prompt(
        TaskType.CLASSIFICATION,
        task_description=_TASK_DESC_CLS,
        rows=[{"text": "สวัสดีตอนเช้า", "label": "unknown"}],
        classification_labels=_CLS_LABELS,
    )
    assert (prompt.system, prompt.user) == snapshot


def test_judge_prompt_qa(snapshot):
    prompt = build_judge_prompt(
        TaskType.QA,
        task_description=_TASK_DESC_QA,
        rows=[{"question": "คืนสินค้ากี่วัน?", "answer": "7 วันหลังได้รับ"}],
    )
    assert (prompt.system, prompt.user) == snapshot


def test_judge_prompt_tool_calling(snapshot):
    prompt = build_judge_prompt(
        TaskType.TOOL_CALLING,
        task_description=_TASK_DESC_TOOL,
        rows=[
            {
                "question": "เปิดเสียงดังหน่อย",
                "answer": '{"name":"set_volume","parameters":{"level":80}}',
            }
        ],
        tool_definitions=_TOOL_DEFS,
    )
    assert (prompt.system, prompt.user) == snapshot


def test_judge_prompt_tool_calling_sentinel(snapshot):
    prompt = build_judge_prompt(
        TaskType.TOOL_CALLING,
        task_description=_TASK_DESC_TOOL,
        rows=[
            {
                "question": "วันนี้อากาศดีไหม",
                "answer": '{"name":"no_tool_needed","parameters":{}}',
            }
        ],
        tool_definitions=_TOOL_DEFS,
    )
    assert (prompt.system, prompt.user) == snapshot


def test_judge_prompt_tool_calling_multi_row_mixed_sentinel(snapshot):
    """3-row batch mixing normal and sentinel rows in one Judge call."""
    prompt = build_judge_prompt(
        TaskType.TOOL_CALLING,
        task_description=_TASK_DESC_TOOL,
        rows=[
            {
                "question": "เปิดเสียงดังหน่อย",
                "answer": '{"name":"set_volume","parameters":{"level":80}}',
            },
            {
                "question": "วันนี้อากาศดีไหม",
                "answer": '{"name":"no_tool_needed","parameters":{}}',
            },
            {
                "question": "ลดเสียงลงหน่อย",
                "answer": '{"name":"set_volume","parameters":{"level":20}}',
            },
        ],
        tool_definitions=_TOOL_DEFS,
    )
    assert (prompt.system, prompt.user) == snapshot


# ---- build_meta_prompt ------------------------------------------------------


@pytest.mark.parametrize("include_unknown", [False, True])
def test_meta_prompt_classification(snapshot, include_unknown: bool):
    prompt = build_meta_prompt(
        TaskType.CLASSIFICATION,
        task_description=_TASK_DESC_CLS,
        classification_labels=_CLS_LABELS,
        include_unknown=include_unknown,
    )
    assert (prompt.system, prompt.user) == snapshot


@pytest.mark.parametrize("include_unknown", [False, True])
def test_meta_prompt_qa(snapshot, include_unknown: bool):
    prompt = build_meta_prompt(
        TaskType.QA,
        task_description=_TASK_DESC_QA,
        include_unknown=include_unknown,
    )
    assert (prompt.system, prompt.user) == snapshot


@pytest.mark.parametrize("include_unknown", [False, True])
def test_meta_prompt_tool_calling(snapshot, include_unknown: bool):
    prompt = build_meta_prompt(
        TaskType.TOOL_CALLING,
        task_description=_TASK_DESC_TOOL,
        tool_definitions=_TOOL_DEFS,
        include_unknown=include_unknown,
    )
    assert (prompt.system, prompt.user) == snapshot


# ---- build_pdf_qa_messages --------------------------------------------------


def test_pdf_qa_messages(snapshot):
    messages = build_pdf_qa_messages(
        task_description=_TASK_DESC_QA,
        num_samples=5,
        pdf_data_url="data:application/pdf;base64,JVBERi0xLjQK<TRUNCATED>",
    )
    assert messages == snapshot


# ---- parse_generator_response (round-trip, no snapshot) ---------------------
# Parse is invertible and small; snapshot would just duplicate the input.


def test_parse_generator_response_plain():
    rows = parse_generator_response('{"samples": [{"a": 1}, {"a": 2}]}')
    assert rows == [{"a": 1}, {"a": 2}]


def test_parse_generator_response_with_markdown_fence():
    rows = parse_generator_response('```json\n{"samples": [{"a": 1}]}\n```')
    assert rows == [{"a": 1}]


def test_parse_generator_response_bad_shape_raises():
    with pytest.raises(ValueError):
        parse_generator_response('{"not_samples": []}')
