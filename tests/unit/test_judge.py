"""Unit tests for JudgeScore.weighted + parse_judge_response."""

from __future__ import annotations

import json

from ai_engine.data_gen.judge import (
    JudgeScore,
    parse_judge_batch_response,
    parse_judge_response,
)


def test_weighted_score_uses_canonical_coefficients():
    s = JudgeScore(fidelity=1.0, naturalness=1.0, utility=1.0, reasoning="")
    assert s.weighted == 1.0

    s = JudgeScore(fidelity=1.0, naturalness=0.0, utility=0.0, reasoning="")
    assert s.weighted == 0.4

    s = JudgeScore(fidelity=0.0, naturalness=1.0, utility=0.0, reasoning="")
    assert s.weighted == 0.3

    s = JudgeScore(fidelity=0.0, naturalness=0.0, utility=1.0, reasoning="")
    assert s.weighted == 0.3


def test_parse_well_formed_response():
    raw = '{"fidelity": 0.8, "naturalness": 0.9, "utility": 0.7, "reasoning": "looks good"}'
    s = parse_judge_response(raw)
    assert s is not None
    assert abs(s.weighted - (0.4 * 0.8 + 0.3 * 0.9 + 0.3 * 0.7)) < 1e-9
    assert s.reasoning == "looks good"


def test_parse_response_with_extra_keys_ignored():
    raw = (
        '{"fidelity": 0.5, "naturalness": 0.5, "utility": 0.5, '
        '"reasoning": "ok", "garbage": 42}'
    )
    s = parse_judge_response(raw)
    assert s is not None
    assert s.weighted == 0.5


def test_parse_out_of_range_score_returns_none():
    raw = '{"fidelity": 1.5, "naturalness": 0.5, "utility": 0.5, "reasoning": ""}'
    assert parse_judge_response(raw) is None


def test_parse_negative_score_returns_none():
    raw = '{"fidelity": -0.1, "naturalness": 0.5, "utility": 0.5, "reasoning": ""}'
    assert parse_judge_response(raw) is None


def test_parse_malformed_json_returns_none():
    assert parse_judge_response("not json at all") is None
    assert parse_judge_response("{partial: json,") is None


def test_parse_empty_returns_none():
    assert parse_judge_response("") is None
    assert parse_judge_response("   ") is None


def _make_entry(index: int, **overrides) -> dict:
    entry = {
        "index": index,
        "reasoning": f"row {index} looks fine",
        "fidelity": 0.8,
        "naturalness": 0.7,
        "utility": 0.6,
    }
    entry.update(overrides)
    return entry


def test_parse_batch_happy_wrapper_shape():
    entries = [_make_entry(i) for i in range(10)]
    raw = json.dumps({"scores": entries})
    results = parse_judge_batch_response(raw, expected=10)
    assert len(results) == 10
    for i, s in enumerate(results):
        assert s is not None
        assert s.reasoning == f"row {i} looks fine"
        assert abs(s.weighted - (0.4 * 0.8 + 0.3 * 0.7 + 0.3 * 0.6)) < 1e-9


def test_parse_batch_happy_bare_array_shape():
    entries = [_make_entry(i) for i in range(10)]
    raw = json.dumps(entries)
    results = parse_judge_batch_response(raw, expected=10)
    assert len(results) == 10
    assert all(s is not None for s in results)


def test_parse_batch_out_of_order_indices():
    entries = [_make_entry(i) for i in reversed(range(10))]
    raw = json.dumps({"scores": entries})
    results = parse_judge_batch_response(raw, expected=10)
    assert len(results) == 10
    for i, s in enumerate(results):
        assert s is not None
        assert s.reasoning == f"row {i} looks fine"


def test_parse_batch_missing_index_leaves_that_slot_none():
    entries = [_make_entry(i) for i in range(10) if i != 3]
    raw = json.dumps({"scores": entries})
    results = parse_judge_batch_response(raw, expected=10)
    assert len(results) == 10
    assert results[3] is None
    for i in range(10):
        if i != 3:
            assert results[i] is not None


def test_parse_batch_extra_unknown_index_ignored():
    entries = [_make_entry(i) for i in range(10)]
    entries.append(_make_entry(42))  # out of [0, expected) range
    raw = json.dumps({"scores": entries})
    results = parse_judge_batch_response(raw, expected=10)
    assert len(results) == 10
    assert all(s is not None for s in results)


def test_parse_batch_malformed_entry_only_affects_that_slot():
    entries = [_make_entry(i) for i in range(10)]
    entries[4]["fidelity"] = 1.5  # out of range -> fails JudgeScore validation
    raw = json.dumps({"scores": entries})
    results = parse_judge_batch_response(raw, expected=10)
    assert len(results) == 10
    assert results[4] is None
    for i in range(10):
        if i != 4:
            assert results[i] is not None


def test_parse_batch_duplicate_index_first_wins():
    entries = [_make_entry(0, reasoning="first"), _make_entry(0, reasoning="second")]
    raw = json.dumps({"scores": entries})
    results = parse_judge_batch_response(raw, expected=1)
    assert len(results) == 1
    assert results[0] is not None
    assert results[0].reasoning == "first"


def test_parse_batch_blank_input_returns_all_none():
    assert parse_judge_batch_response("", expected=10) == [None] * 10
    assert parse_judge_batch_response("   ", expected=10) == [None] * 10
    assert parse_judge_batch_response("not json at all", expected=10) == [None] * 10


def test_parse_batch_reasoning_truncated_not_dropped():
    long_reasoning = "x" * 500
    entries = [_make_entry(0, reasoning=long_reasoning)]
    raw = json.dumps({"scores": entries})
    results = parse_judge_batch_response(raw, expected=1)
    assert results[0] is not None
    assert results[0].reasoning == long_reasoning[:120]
    assert len(results[0].reasoning) == 120
