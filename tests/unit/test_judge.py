"""Unit tests for JudgeScore.weighted + parse_judge_response."""

from __future__ import annotations

from ai_engine.data_gen.judge import JudgeScore, parse_judge_response


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
