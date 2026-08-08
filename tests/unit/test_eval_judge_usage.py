"""The evaluation LLM judge must be metered and breaker-protected.

Until 2026-08-08 `workers/tasks/evaluation.py` was the one OpenRouter caller
in the codebase that spent real money with:

  * no usage recorded — so `GET /usage` and the monthly budget cap simply
    could not see evaluation spend, which made "all OpenRouter spend is
    accounted for" (gap item 12, marked ✅) untrue; and
  * no circuit-breaker hooks — so it kept calling a provider already known
    to be down, and its own failures counted toward tripping nothing.

These tests cover the seams that fix carries. They deliberately exercise
`judge_rows` / `_apply_llm_judge` / `_build_judge_client` directly rather
than standing up the whole Celery task: the task-level harness (EvaluationRun
→ ModelArtifact → TrainingJob → Dataset + a fake Ollama) tests the
orchestration, while what regressed here is metering at the call site.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from ai_engine.data_gen.usage import (
    STAGE_EVAL_JUDGE,
    SDGBudgetExceededError,
    UsageAccumulator,
)
from ai_engine.evaluation.llm_judge import judge_rows

_JUDGE_MODEL = "test/judge-model"
# `UsageAccumulator` prices are USD **per token**, not per 1M — the /1e6
# conversion happens upstream in `model_pricing.price_for`. Using round
# per-token numbers here keeps the budget arithmetic below readable:
# one row (100 prompt + 50 completion) costs 100*1 + 50*2 = $150.
_PRICES = {_JUDGE_MODEL: (1.0, 2.0)}
_COST_PER_ROW = 150.0


@dataclass
class _FakeChat:
    content: str
    model: str
    finish_reason: str | None = "stop"
    prompt_tokens: int | None = 100
    completion_tokens: int | None = 50


class _FakeClient:
    """Minimal stand-in for `OpenRouterClient` — records calls, returns canned
    responses in order. Anything the judge does beyond `.chat()` is out of
    scope for these tests."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def chat(self, **_kwargs: Any) -> _FakeChat:
        self.calls += 1
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def _ok(score: int = 4) -> _FakeChat:
    return _FakeChat(content=f'{{"score": {score}, "reason": "fine"}}', model=_JUDGE_MODEL)


# ---- metering ---------------------------------------------------------------


def test_judge_rows_records_usage_under_the_eval_judge_stage() -> None:
    usage = UsageAccumulator(prices=_PRICES)
    client = _FakeClient([_ok(), _ok(), _ok()])

    judge_rows(
        client=client,
        judge_model=_JUDGE_MODEL,
        questions=["q1", "q2", "q3"],
        expected=["e1", "e2", "e3"],
        predicted=["p1", "p2", "p3"],
        usage=usage,
    )

    entries = usage.entries()
    assert entries, "judge recorded no usage at all — the metering hole is back"
    assert len(entries) == 1, f"expected one (model, stage) bucket, got {entries}"
    entry = entries[0]
    assert entry.model == _JUDGE_MODEL
    # Not STAGE_JUDGE: that is SDG's own generation-time judge. Collapsing
    # the two would make per-stage cost reporting unable to separate them.
    assert entry.stage == STAGE_EVAL_JUDGE
    assert entry.prompt_tokens == 300
    assert entry.completion_tokens == 150


def test_a_row_whose_json_fails_to_parse_is_still_billed() -> None:
    """The row is `skipped` for scoring, but the API call was paid for.

    Billing only the parse-success path would systematically under-report
    exactly the runs that are going wrong — the opposite of what a budget
    is for.
    """
    usage = UsageAccumulator(prices=_PRICES)
    unparseable = _FakeChat(content="not json at all", model=_JUDGE_MODEL)
    client = _FakeClient([_ok(), unparseable])

    result = judge_rows(
        client=client,
        judge_model=_JUDGE_MODEL,
        questions=["q1", "q2"],
        expected=["e1", "e2"],
        predicted=["p1", "p2"],
        usage=usage,
    )

    assert result.skipped == 1, "the unparseable row should not have scored"
    entry = usage.entries()[0]
    assert entry.prompt_tokens == 200, (
        "the unparseable row was not billed — usage is being recorded after "
        "the parse instead of after the API call"
    )


def test_a_row_whose_api_call_raises_bills_nothing_for_that_row() -> None:
    """The complement of the test above: no response, no tokens, no charge."""
    usage = UsageAccumulator(prices=_PRICES)
    client = _FakeClient([_ok(), RuntimeError("connection reset")])

    result = judge_rows(
        client=client,
        judge_model=_JUDGE_MODEL,
        questions=["q1", "q2"],
        expected=["e1", "e2"],
        predicted=["p1", "p2"],
        usage=usage,
    )

    assert result.skipped == 1
    assert usage.entries()[0].prompt_tokens == 100


def test_judge_runs_unmetered_when_no_accumulator_is_passed() -> None:
    """`usage=None` must stay a working call path, not an AttributeError."""
    client = _FakeClient([_ok()])
    result = judge_rows(
        client=client,
        judge_model=_JUDGE_MODEL,
        questions=["q"],
        expected=["e"],
        predicted=["p"],
    )
    assert result.mean_score == 4


# ---- budget ------------------------------------------------------------------


def test_budget_stops_the_judge_mid_pass() -> None:
    """A 500-row judge pass is exactly what a submit-only budget check
    cannot stop, so the cap is enforced per row as tokens accumulate."""
    # A budget of 1.5 rows survives row 1 and breaches on row 2 — which is
    # the point: the breach is detected mid-pass, not at submit.
    usage = UsageAccumulator(
        prices=_PRICES, budget_remaining_usd=_COST_PER_ROW * 1.5
    )
    client = _FakeClient([_ok() for _ in range(10)])

    with pytest.raises(SDGBudgetExceededError):
        judge_rows(
            client=client,
            judge_model=_JUDGE_MODEL,
            questions=["q"] * 10,
            expected=["e"] * 10,
            predicted=["p"] * 10,
            usage=usage,
        )

    assert client.calls == 2, (
        f"judge made {client.calls} calls before the budget stopped it — the "
        "cap is not being checked per row"
    )


def test_an_unlimited_budget_does_not_stop_the_judge() -> None:
    usage = UsageAccumulator(prices=_PRICES, budget_remaining_usd=None)
    client = _FakeClient([_ok() for _ in range(5)])

    judge_rows(
        client=client,
        judge_model=_JUDGE_MODEL,
        questions=["q"] * 5,
        expected=["e"] * 5,
        predicted=["p"] * 5,
        usage=usage,
    )
    assert client.calls == 5


# ---- circuit breaker wiring --------------------------------------------------


def test_build_judge_client_wires_all_three_breaker_hooks() -> None:
    """Enumerated over the kwargs actually handed to `OpenRouterClient`.

    All three, not two: round 2 shipped `record_success` unwired on the SDG
    client, which meant the breaker counted *cumulative* failures and could
    never close once open (see the circuit-breaker row in TASK_TRACKER.md).
    A guard that only checked the two failure-side hooks would have passed
    against that bug.
    """
    from api.services import circuit_breaker
    from workers.tasks import evaluation as eval_module

    captured: dict[str, Any] = {}

    class _Spy:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    import ai_engine.data_gen.openrouter_client as orc

    original = orc.OpenRouterClient
    orc.OpenRouterClient = _Spy  # type: ignore[misc,assignment]
    try:
        settings = type(
            "S",
            (),
            {
                "openrouter_api_key": "sk-or-v1-test",
                "openrouter_http_referer": "http://test",
                "openrouter_app_title": "test",
            },
        )()
        eval_module._build_judge_client(settings, _JUDGE_MODEL)
    finally:
        orc.OpenRouterClient = original  # type: ignore[misc]

    assert captured, "no kwargs captured — the spy never ran, guard is vacuous"
    assert captured.get("precheck") is circuit_breaker.precheck, (
        "judge client has no `precheck` — evaluation will keep calling an "
        "OpenRouter that the breaker already knows is down"
    )
    assert captured.get("on_call_failure") is circuit_breaker.on_failure, (
        "judge client has no `on_call_failure` — evaluation's outages count "
        "toward tripping nothing"
    )
    assert captured.get("on_call_success") is circuit_breaker.record_success, (
        "judge client has no `on_call_success` — an open breaker can never "
        "close again on evaluation traffic (the round-2 dead-hook bug)"
    )


# ---- the worker seam ---------------------------------------------------------


def test_apply_llm_judge_threads_the_accumulator_through() -> None:
    """The task builds the accumulator; this is the wire that carries it."""
    from api.schemas.enums import TaskType
    from workers.tasks import evaluation as eval_module

    usage = UsageAccumulator(prices=_PRICES)
    client = _FakeClient([_ok(), _ok()])
    eval_module._build_judge_client = lambda *_a, **_k: client  # type: ignore[assignment]

    settings = type("S", (), {"llm_judge_model": _JUDGE_MODEL})()
    metrics: dict[str, Any] = {}
    try:
        score, model = eval_module._apply_llm_judge(
            use_llm_judge=True,
            task_type=TaskType.QA,
            judge_model=None,
            settings=settings,
            questions=["q1", "q2"],
            expected=["e1", "e2"],
            predicted=["p1", "p2"],
            metrics=metrics,
            usage=usage,
        )
    finally:
        import importlib

        importlib.reload(eval_module)

    assert score == 4
    assert model == _JUDGE_MODEL
    entries = usage.entries()
    assert entries and entries[0].stage == STAGE_EVAL_JUDGE, (
        "_apply_llm_judge did not pass `usage` down to judge_rows — the task "
        "would build an accumulator that stays empty and bill $0 for every run"
    )
