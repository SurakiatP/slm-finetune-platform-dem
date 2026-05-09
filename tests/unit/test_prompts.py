"""Unit tests for the new RTC-FO prompt builders."""

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


# ---- Generator prompts ---------------------------------------------------


def test_generator_prompt_classification_includes_labels_and_target():
    p = build_generator_prompt(
        TaskType.CLASSIFICATION,
        task_description="classify support tickets",
        label_or_tool="billing",
        examples=[{"text": "I want a refund", "label": "billing"}],
        diversity_rule="vary register",
        difficulty="medium",
        classification_labels=["billing", "tech", "general"],
    )
    assert "CLASSIFICATION" in p.system
    assert "billing" in p.user
    assert "tech" in p.user
    assert "vary register" in p.user
    assert "medium" in p.user


def test_generator_prompt_sentinel_classification_does_not_assert_real_label():
    p = build_generator_prompt(
        TaskType.CLASSIFICATION,
        task_description="...",
        label_or_tool="unknown",
        examples=[],
        diversity_rule="off-topic",
        difficulty="easy",
        classification_labels=["billing", "tech", "general", "unknown"],
        is_sentinel=True,
    )
    # Sentinel branch tells the model the input should NOT match real labels.
    assert "DO NOT" in p.user or "do not" in p.user.lower()


def test_generator_prompt_tool_calling_includes_tool_catalog():
    tools = [
        ToolDefinition(
            name="set_oven",
            description="Set oven temperature",
            parameters={"celsius": ToolParameterSpec(type="integer", required=True)},
        )
    ]
    p = build_generator_prompt(
        TaskType.TOOL_CALLING,
        task_description="kitchen instructions",
        label_or_tool="set_oven",
        examples=None,
        diversity_rule="formal phrasing",
        difficulty="hard",
        tool_definitions=tools,
    )
    assert "set_oven" in p.user
    assert "celsius" in p.user


def test_generator_prompt_qa_no_label_no_tools():
    p = build_generator_prompt(
        TaskType.QA,
        task_description="answer policy questions",
        label_or_tool=None,
        examples=[{"question": "q?", "answer": "a"}],
        diversity_rule="mix length",
        difficulty="easy",
    )
    assert "QUESTION-ANSWERING" in p.system
    assert "[Target label]" not in p.user
    assert "[Target tool]" not in p.user
    assert "answer policy questions" in p.user


def test_generator_prompt_includes_candidates_count():
    """Output instructions must lock in the 5-per-call expectation."""
    from ai_engine.data_gen.constants import CANDIDATES_PER_GEN_CALL

    p = build_generator_prompt(
        TaskType.QA,
        task_description="x",
        label_or_tool=None,
        examples=[],
        diversity_rule="r",
        difficulty="easy",
    )
    assert str(CANDIDATES_PER_GEN_CALL) in p.user


# ---- Judge prompts -------------------------------------------------------


def test_judge_prompt_qa_uses_correctness_rubric():
    p = build_judge_prompt(
        TaskType.QA,
        task_description="answer policy questions",
        row={"question": "What is the return window?", "answer": "30 days."},
    )
    assert "fidelity" in p.user
    assert "answer" in p.user.lower()


def test_judge_prompt_classification_includes_labels():
    p = build_judge_prompt(
        TaskType.CLASSIFICATION,
        task_description="classify tickets",
        row={"text": "refund please", "label": "billing"},
        classification_labels=["billing", "tech"],
    )
    assert "billing" in p.user
    assert "tech" in p.user


def test_judge_prompt_outputs_only_json_object():
    p = build_judge_prompt(
        TaskType.QA, task_description="x", row={"question": "q", "answer": "a"}
    )
    # The Judge prompt MUST tell the model "output ONLY a JSON object".
    assert "ONLY" in p.user.upper().replace("ONLY", "ONLY")  # presence check
    assert '"fidelity"' in p.user
    assert '"naturalness"' in p.user
    assert '"utility"' in p.user


# ---- Meta-prompter prompts -----------------------------------------------


def test_meta_prompt_includes_unknown_branch_for_classification():
    p = build_meta_prompt(
        TaskType.CLASSIFICATION,
        task_description="classify tickets",
        classification_labels=["billing", "tech"],
        include_unknown=True,
    )
    assert "unknown_diversity_rules" in p.user
    assert "OFF-TOPIC" in p.user or "off-topic" in p.user.lower()


def test_meta_prompt_qa_branch_omits_unknown():
    p = build_meta_prompt(
        TaskType.QA, task_description="answer questions", include_unknown=False
    )
    assert "unknown_diversity_rules" not in p.user


# ---- PDF→QA messages ----------------------------------------------------


def test_pdf_qa_messages_have_text_and_file_parts():
    msgs = build_pdf_qa_messages(
        task_description="answer questions about the document",
        num_samples=10,
        pdf_data_url="data:application/pdf;base64,aGVsbG8=",
    )
    assert msgs[0]["role"] == "system"
    user_msg = msgs[1]
    assert user_msg["role"] == "user"
    parts = user_msg["content"]
    types = [p["type"] for p in parts]
    assert "text" in types
    assert "file" in types
    file_part = next(p for p in parts if p["type"] == "file")
    assert file_part["file"]["file_data"].startswith("data:application/pdf;base64,")


# ---- Generator response parsing ------------------------------------------


def test_parse_well_formed_response():
    raw = '{"samples": [{"text": "hi", "label": "greet"}]}'
    rows = parse_generator_response(raw)
    assert rows == [{"text": "hi", "label": "greet"}]


def test_parse_with_markdown_fences():
    raw = '```json\n{"samples": [{"text": "x", "label": "y"}]}\n```'
    rows = parse_generator_response(raw)
    assert rows == [{"text": "x", "label": "y"}]


def test_parse_missing_samples_key_raises():
    with pytest.raises(ValueError):
        parse_generator_response('{"foo": "bar"}')


def test_parse_samples_not_array_raises():
    with pytest.raises(ValueError):
        parse_generator_response('{"samples": "nope"}')


def test_parse_drops_non_dict_elements():
    raw = '{"samples": [{"a": 1}, "skip me", null, {"a": 2}]}'
    rows = parse_generator_response(raw)
    assert rows == [{"a": 1}, {"a": 2}]
