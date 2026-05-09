"""Unit tests for meta_prompter parse + fallback."""

from __future__ import annotations

import json

from ai_engine.data_gen.meta_prompter import (
    SDGRules,
    fallback_rules,
    parse_meta_response,
)


def test_well_formed_response_with_unknown():
    payload = {
        "diversity_rules": [f"rule{i}" for i in range(8)],
        "unknown_diversity_rules": [f"unk{i}" for i in range(5)],
    }
    rules = parse_meta_response(json.dumps(payload), include_unknown=True)
    assert isinstance(rules, SDGRules)
    assert len(rules.diversity_rules) == 8
    assert len(rules.unknown_diversity_rules) == 5


def test_well_formed_response_without_unknown_for_qa():
    """QA path: include_unknown=False — empty unknown list is fine."""
    payload = {"diversity_rules": [f"r{i}" for i in range(10)]}
    rules = parse_meta_response(json.dumps(payload), include_unknown=False)
    assert len(rules.diversity_rules) == 10
    assert rules.unknown_diversity_rules == []


def test_malformed_json_falls_back():
    rules = parse_meta_response("not json", include_unknown=True)
    assert isinstance(rules, SDGRules)
    assert len(rules.diversity_rules) >= 8
    assert len(rules.unknown_diversity_rules) >= 5


def test_short_diversity_list_falls_back():
    """Less than 8 rules should fail validation → fallback."""
    payload = {"diversity_rules": ["only one"]}
    rules = parse_meta_response(json.dumps(payload), include_unknown=False)
    assert len(rules.diversity_rules) >= 8


def test_missing_unknown_when_required_pads_from_generic():
    """If LLM forgets to emit unknown_diversity_rules but caller needs them,
    we pad from the generic fallback list rather than fail entirely.
    """
    payload = {"diversity_rules": [f"r{i}" for i in range(8)]}
    rules = parse_meta_response(json.dumps(payload), include_unknown=True)
    assert len(rules.unknown_diversity_rules) >= 5


def test_empty_response_falls_back():
    rules = parse_meta_response("", include_unknown=True)
    assert len(rules.diversity_rules) >= 8


def test_explicit_fallback_helper():
    r = fallback_rules(include_unknown=True)
    assert len(r.diversity_rules) >= 8
    assert len(r.unknown_diversity_rules) >= 5
    r = fallback_rules(include_unknown=False)
    assert r.unknown_diversity_rules == []
