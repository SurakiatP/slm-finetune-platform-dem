"""Tier 1+2 characterization snapshots for Node 9 (Evaluation rule-based).

Wraps the pure helpers + Ollama-mediated prediction loop from
`workers/tasks/evaluation.py` BEFORE refactoring that file. Snapshot diff = 0
post-refactor ⇒ orchestration behaviour preserved.

The `_predict_rows` tests use respx to mock the Ollama HTTP layer
deterministically — the actual ML inference is irrelevant to characterisation
of the orchestration boundary.

See `tests/fixtures/baseline/node-9/README.md` for the Tier 3 contract
(schema baseline from Session 26 vast.ai run).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from api.schemas.data_formats import ToolDefinition
from api.schemas.enums import TaskType
from workers.tasks.evaluation import (
    _extract_labels,
    _extract_tool_definitions,
    _postprocess,
    _predict_rows,
    _prompt_and_gold,
)

_OLLAMA_BASE = "http://ollama-test:11434"


def _ollama_chat_response(content: str) -> dict:
    return {
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            },
        ],
    }


def _tool(name: str, param_name: str, param_type: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"Test tool: {name}",
        parameters={param_name: {"type": param_type}},
    )


# ---- Tier 1: pure helper snapshots -----------------------------------------


def test_prompt_and_gold_classification(snapshot):
    row = {"text": "ลืมรหัสผ่าน", "label": "ปัญหาเทคนิค"}
    out = _prompt_and_gold(row, TaskType.CLASSIFICATION, None)
    assert out == snapshot


def test_prompt_and_gold_qa(snapshot):
    row = {"question": "เมืองหลวงของไทย?", "answer": "กรุงเทพ"}
    out = _prompt_and_gold(row, TaskType.QA, None)
    assert out == snapshot


def test_prompt_and_gold_tool_calling_with_definitions(snapshot):
    tools = [
        _tool("set_volume", "level", "integer"),
        _tool("play_music", "track", "string"),
    ]
    row = {
        "question": "เปิดเพลง",
        "answer": '{"name": "play_music", "parameters": {"track": "default"}}',
    }
    out = _prompt_and_gold(row, TaskType.TOOL_CALLING, tools)
    assert out == snapshot


def test_prompt_and_gold_tool_calling_no_definitions(snapshot):
    row = {
        "question": "เปิดเพลง",
        "answer": '{"name": "play_music", "parameters": {}}',
    }
    out = _prompt_and_gold(row, TaskType.TOOL_CALLING, None)
    assert out == snapshot


def test_postprocess_classification_multiline(snapshot):
    out = _postprocess("ปัญหาเทคนิค\nเพิ่มเติม: ระบบล่ม", TaskType.CLASSIFICATION)
    assert out == snapshot


def test_postprocess_classification_leading_whitespace(snapshot):
    out = _postprocess("  ปัญหาเทคนิค  ", TaskType.CLASSIFICATION)
    assert out == snapshot


def test_postprocess_qa_unchanged(snapshot):
    out = _postprocess("กรุงเทพมหานคร", TaskType.QA)
    assert out == snapshot


def test_postprocess_qa_strips_whitespace(snapshot):
    out = _postprocess("  คำตอบเต็มประโยค  \n", TaskType.QA)
    assert out == snapshot


def test_postprocess_empty_classification(snapshot):
    out = _postprocess("", TaskType.CLASSIFICATION)
    assert out == snapshot


def test_extract_tool_definitions_present(snapshot):
    meta = {
        "tool_definitions": [
            {
                "name": "set_volume",
                "description": "set the speaker volume",
                "parameters": {"level": {"type": "integer"}},
            },
        ]
    }
    out = _extract_tool_definitions(meta)
    assert [t.model_dump(mode="json") for t in out] == snapshot


def test_extract_tool_definitions_none(snapshot):
    samples = {
        "metadata_is_none": _extract_tool_definitions(None),
        "metadata_without_key": _extract_tool_definitions({"foo": "bar"}),
        "metadata_empty_list": _extract_tool_definitions({"tool_definitions": []}),
    }
    assert samples == snapshot


def test_extract_labels_present(snapshot):
    meta = {"classification_config": {"labels": ["a", "b", "c"]}}
    out = _extract_labels(meta)
    assert out == snapshot


def test_extract_labels_missing(snapshot):
    samples = {
        "metadata_is_none": _extract_labels(None),
        "no_cls_config": _extract_labels({"foo": "bar"}),
        "cls_config_empty": _extract_labels({"classification_config": {}}),
        "labels_not_list": _extract_labels(
            {"classification_config": {"labels": "not-a-list"}}
        ),
    }
    assert samples == snapshot


# ---- Tier 2: respx-mocked Ollama orchestration -----------------------------


@pytest.fixture
def mock_ollama():
    """Mount respx mock on /v1/chat/completions."""
    with respx.mock(base_url=_OLLAMA_BASE, assert_all_called=False) as router:
        yield router


def test_predict_rows_classification(snapshot, mock_ollama):
    rows = [
        {"text": "ลืมรหัสผ่าน", "label": "ปัญหาเทคนิค"},
        {"text": "ค่าบริการผิด", "label": "ปัญหาการเงิน"},
        {"text": "ใช้งานยังไง", "label": "คำถามทั่วไป"},
    ]
    responses = ["ปัญหาเทคนิค", "ปัญหาการเงิน  ", "คำถามทั่วไป\nอีกบรรทัด"]
    mock_ollama.post("/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json=_ollama_chat_response(c)) for c in responses
        ]
    )
    predicted, expected, questions = _predict_rows(
        rows=rows,
        task_type=TaskType.CLASSIFICATION,
        tool_definitions=None,
        ollama_base_url=_OLLAMA_BASE,
        ollama_tag="slm/test-tag",
    )
    assert {
        "predicted": predicted,
        "expected": expected,
        "questions": questions,
    } == snapshot


def test_predict_rows_qa(snapshot, mock_ollama):
    rows = [
        {"question": "เมืองหลวงไทย?", "answer": "กรุงเทพ"},
        {"question": "2+2=?", "answer": "4"},
    ]
    responses = ["กรุงเทพมหานคร", "4 ครับ"]
    mock_ollama.post("/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json=_ollama_chat_response(c)) for c in responses
        ]
    )
    predicted, expected, questions = _predict_rows(
        rows=rows,
        task_type=TaskType.QA,
        tool_definitions=None,
        ollama_base_url=_OLLAMA_BASE,
        ollama_tag="slm/test-tag",
    )
    assert {
        "predicted": predicted,
        "expected": expected,
        "questions": questions,
    } == snapshot


def test_predict_rows_tool_calling(snapshot, mock_ollama):
    tools = [
        _tool("set_volume", "level", "integer"),
        _tool("play_music", "track", "string"),
    ]
    rows = [
        {
            "question": "ตั้งเสียง 50",
            "answer": '{"name": "set_volume", "parameters": {"level": 50}}',
        },
        {
            "question": "เล่นเพลง",
            "answer": '{"name": "play_music", "parameters": {"track": "song"}}',
        },
    ]
    responses = [
        '{"name": "set_volume", "parameters": {"level": 50}}',
        '{"name": "play_music", "parameters": {"track": "song"}}',
    ]
    mock_ollama.post("/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json=_ollama_chat_response(c)) for c in responses
        ]
    )
    predicted, expected, questions = _predict_rows(
        rows=rows,
        task_type=TaskType.TOOL_CALLING,
        tool_definitions=tools,
        ollama_base_url=_OLLAMA_BASE,
        ollama_tag="slm/test-tag",
    )
    assert {
        "predicted": predicted,
        "expected": expected,
        "questions": questions,
    } == snapshot


def test_predict_rows_ollama_500_treated_as_empty(snapshot, mock_ollama):
    rows = [
        {"text": "row1", "label": "A"},
        {"text": "row2", "label": "B"},
    ]
    mock_ollama.post("/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(500, json={"error": "ollama down"}),
            httpx.Response(200, json=_ollama_chat_response("B")),
        ]
    )
    predicted, expected, questions = _predict_rows(
        rows=rows,
        task_type=TaskType.CLASSIFICATION,
        tool_definitions=None,
        ollama_base_url=_OLLAMA_BASE,
        ollama_tag="slm/test-tag",
    )
    assert {
        "predicted": predicted,
        "expected": expected,
        "questions": questions,
    } == snapshot
