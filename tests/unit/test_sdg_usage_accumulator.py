"""Unit tests for `ai_engine.data_gen.usage.UsageAccumulator`.

Proves: (1) per-call `add()` aggregates into per-(model, stage) rows rather
than growing one row per call, (2) `check_budget()` fires exactly at the
threshold and never before, (3) a `None` budget never raises, (4) unpriced
models are flagged via `has_unpriced_usage` without corrupting `spent_usd`,
and (5) the module stays free of FastAPI/Celery/SQLAlchemy/api.core.config
imports per the hexagonal architecture rule.

Pure unit tests — no DB, no Docker, no GPU, no real OpenRouter calls.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ai_engine.data_gen.usage import (
    STAGE_GENERATE,
    STAGE_JUDGE,
    SDGBudgetExceededError,
    UsageAccumulator,
)

MODEL_A = "deepseek/deepseek-v4-flash-0731"
MODEL_B = "google/gemini-2.5-flash-lite"


def test_many_calls_aggregate_into_one_entry_per_model_stage_pair() -> None:
    """200 add() calls across 2 models x 2 stages must collapse to 4 entries."""
    acc = UsageAccumulator()

    calls = [
        (MODEL_A, STAGE_GENERATE),
        (MODEL_A, STAGE_JUDGE),
        (MODEL_B, STAGE_GENERATE),
        (MODEL_B, STAGE_JUDGE),
    ]
    # 200 calls total, 50 per (model, stage) pair, each contributing
    # 10 prompt tokens + 5 completion tokens.
    for _ in range(50):
        for model, stage in calls:
            acc.add(model, stage, 10, 5)

    entries = acc.entries()
    assert len(entries) == 4

    by_key = {(e.model, e.stage): e for e in entries}
    for model, stage in calls:
        entry = by_key[(model, stage)]
        assert entry.prompt_tokens == 500
        assert entry.completion_tokens == 250


def test_add_coerces_none_token_counts_to_zero() -> None:
    acc = UsageAccumulator()
    acc.add(MODEL_A, STAGE_GENERATE, None, None)
    acc.add(MODEL_A, STAGE_GENERATE, 10, None)

    entries = acc.entries()
    assert len(entries) == 1
    assert entries[0].prompt_tokens == 10
    assert entries[0].completion_tokens == 0


def test_check_budget_raises_at_threshold_and_never_before() -> None:
    prices = {MODEL_A: (0.01, 0.02)}  # $/token
    acc = UsageAccumulator(prices=prices, budget_remaining_usd=1.0)

    # 40 prompt + 10 completion tokens => 40*0.01 + 10*0.02 = 0.6 => under budget.
    acc.add(MODEL_A, STAGE_GENERATE, 40, 10)
    acc.check_budget()  # must not raise

    # Add more to land exactly at $1.00: need additional 0.4 usd.
    # 20 prompt tokens * 0.01 = 0.2, 10 completion tokens * 0.02 = 0.2 -> +0.4
    acc.add(MODEL_A, STAGE_GENERATE, 20, 10)
    assert acc.spent_usd == pytest.approx(1.0)

    with pytest.raises(SDGBudgetExceededError) as exc_info:
        acc.check_budget()

    message = str(exc_info.value)
    assert "1.0000" in message or "$1.0000" in message
    assert "spent" in message.lower()
    # Both the amount spent and the limit must be present in the message.
    assert f"{acc.spent_usd:.4f}" in message


def test_check_budget_none_never_raises() -> None:
    acc = UsageAccumulator(budget_remaining_usd=None)
    acc.add(MODEL_A, STAGE_GENERATE, 10_000_000, 10_000_000)
    acc.check_budget()  # no-op regardless of spend


def test_unpriced_model_sets_flag_and_does_not_change_spent_usd() -> None:
    prices = {MODEL_A: (0.01, 0.02)}
    acc = UsageAccumulator(prices=prices)

    acc.add(MODEL_A, STAGE_GENERATE, 100, 100)
    spent_before = acc.spent_usd
    assert acc.has_unpriced_usage is False

    acc.add(MODEL_B, STAGE_JUDGE, 500, 500)  # unpriced model
    assert acc.has_unpriced_usage is True
    assert acc.spent_usd == pytest.approx(spent_before)


def test_module_has_no_forbidden_imports() -> None:
    """Hexagonal rule: ai_engine/ must never import api/fastapi/celery/sqlalchemy."""
    usage_py = Path(__file__).resolve().parents[2] / "ai_engine" / "data_gen" / "usage.py"
    assert usage_py.exists()

    grep = subprocess.run(
        ["grep", "-E", r"^(from|import) (api|fastapi|celery|sqlalchemy)", str(usage_py)],
        capture_output=True,
        text=True,
    )
    assert grep.returncode != 0, f"forbidden imports found:\n{grep.stdout}"
    assert grep.stdout == ""
