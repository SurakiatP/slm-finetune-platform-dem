"""Tier 1+2 characterization snapshots for Node 10 (LLM Judge).

Wraps the pure helpers in ``ai_engine/evaluation/llm_judge.py`` plus the
``_apply_llm_judge`` orchestrator in ``workers/tasks/evaluation.py`` BEFORE
refactoring those files. Snapshot diff = 0 post-refactor ⇒ behaviour preserved.

Tier 1: pure response-parser (``_parse_judge_response``) + ``judge_rows``
validation/aggregation.

Tier 2: orchestrator ``_apply_llm_judge`` invoked with a stubbed
``OpenRouterClient.chat`` so the full code path (judge model resolution,
client construction, per-row scoring, mean aggregation, skipped-row
counting, metrics mutation) runs end-to-end without OpenRouter.

Branch stack: this file lives on ``feature/refactor-node-10-judge`` which is
based on ``feature/refactor-node-9-eval``; both Node-9 helpers
(``_compute_metrics_for_task``, ``_apply_llm_judge``) are already extracted.
"""

from __future__ import annotations

from dataclasses import asdict
from types import SimpleNamespace

import pytest

from ai_engine.data_gen.openrouter_client import ChatResult, OpenRouterClient
from ai_engine.evaluation.llm_judge import (
    _parse_judge_response,
    judge_rows,
)
from api.schemas.enums import TaskType
from workers.tasks import evaluation as eval_mod


# ---------------------------------------------------------------------------
# Tier 1 — pure helpers: _parse_judge_response
# ---------------------------------------------------------------------------


def test_parse_judge_response_plain_json(snapshot):
    out = _parse_judge_response('{"score": 4, "reason": "mostly correct"}')
    assert out == snapshot


def test_parse_judge_response_markdown_fence_json(snapshot):
    raw = '```json\n{"score": 5, "reason": "perfect"}\n```'
    out = _parse_judge_response(raw)
    assert out == snapshot


def test_parse_judge_response_markdown_fence_no_lang(snapshot):
    raw = '```\n{"score": 2, "reason": "wrong-ish"}\n```'
    out = _parse_judge_response(raw)
    assert out == snapshot


def test_parse_judge_response_reason_missing_defaults_empty(snapshot):
    out = _parse_judge_response('{"score": 3}')
    assert out == snapshot


def test_parse_judge_response_reason_truncated_to_500_chars(snapshot):
    long_reason = "x" * 1000
    score, reason = _parse_judge_response(f'{{"score": 4, "reason": "{long_reason}"}}')
    # snapshot the structural facts, not the 500-char blob
    assert {"score": score, "reason_len": len(reason), "reason_head": reason[:8]} == snapshot


def test_parse_judge_response_invalid_cases_raise(snapshot):
    """Group the error paths so the snapshot doubles as the contract doc."""
    cases: dict[str, str] = {}
    for name, raw in [
        ("not_a_dict", "[1, 2, 3]"),
        ("score_out_of_range_high", '{"score": 9, "reason": "x"}'),
        ("score_out_of_range_low", '{"score": 0, "reason": "x"}'),
        ("score_missing", '{"reason": "x"}'),
        ("score_not_int", '{"score": "four", "reason": "x"}'),
        ("malformed_json", "{not json"),
    ]:
        try:
            _parse_judge_response(raw)
            cases[name] = "DID_NOT_RAISE"
        except Exception as exc:
            cases[name] = f"{type(exc).__name__}: {str(exc)[:80]}"
    assert cases == snapshot


# ---------------------------------------------------------------------------
# Tier 1 — judge_rows validation
# ---------------------------------------------------------------------------


def test_judge_rows_length_mismatch_raises(snapshot):
    try:
        judge_rows(
            client=object(),  # type: ignore[arg-type]  # unreached
            judge_model="m",
            questions=["q1", "q2"],
            expected=["e1"],
            predicted=["p1"],
        )
        result = "DID_NOT_RAISE"
    except ValueError as exc:
        result = f"ValueError: {exc}"
    assert result == snapshot


def test_judge_rows_empty_raises(snapshot):
    try:
        judge_rows(
            client=object(),  # type: ignore[arg-type]
            judge_model="m",
            questions=[],
            expected=[],
            predicted=[],
        )
        result = "DID_NOT_RAISE"
    except ValueError as exc:
        result = f"ValueError: {exc}"
    assert result == snapshot


# ---------------------------------------------------------------------------
# Tier 2 helpers — stub OpenRouter sync client
# ---------------------------------------------------------------------------


class _ScriptedChat:
    """Captures kwargs of each ``OpenRouterClient.chat`` call and returns scripted ChatResult."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self._idx = 0

    def __call__(self, instance, **kwargs):  # signature matches bound method
        self.calls.append(kwargs)
        if self._idx >= len(self._responses):
            raise RuntimeError("_ScriptedChat: ran out of responses")
        r = self._responses[self._idx]
        self._idx += 1
        if isinstance(r, Exception):
            raise r
        return r


def _result(content: str, model: str = "stub-judge-model") -> ChatResult:
    return ChatResult(
        content=content,
        model=model,
        finish_reason="stop",
        prompt_tokens=10,
        completion_tokens=5,
    )


@pytest.fixture
def stub_openrouter(monkeypatch):
    """Replace ``OpenRouterClient.__init__`` (skip api-key check) and ``.chat``.

    Returns a builder ``install(responses) -> _ScriptedChat`` so each test
    scripts its own response sequence. ``responses`` may contain either
    ``ChatResult`` instances or ``Exception`` instances (the latter are
    raised, exercising ``judge_rows``'s per-row try/except).
    """

    def _no_init(self, **_kwargs):  # noqa: ANN001 — mimics __init__ signature
        # Skip the real httpx client construction; we only need an object
        # whose ``.chat`` resolves to our scripted method.
        self._teacher_model = _kwargs.get("teacher_model", "noop")

    monkeypatch.setattr(OpenRouterClient, "__init__", _no_init, raising=True)

    def _install(responses):
        scripted = _ScriptedChat(responses)

        def _bound_chat(self, **kwargs):
            return scripted(self, **kwargs)

        monkeypatch.setattr(OpenRouterClient, "chat", _bound_chat, raising=True)
        return scripted

    return _install


def _settings_for_judge() -> SimpleNamespace:
    """Minimal duck-typed Settings the orchestrator reads."""
    return SimpleNamespace(
        openrouter_api_key="sk-stub",
        openrouter_http_referer="http://test.local",
        openrouter_app_title="slm-platform-test",
        llm_judge_model="openrouter/auto-from-settings",
    )


# ---------------------------------------------------------------------------
# Tier 2 — _apply_llm_judge orchestrator
# ---------------------------------------------------------------------------


def test_apply_llm_judge_disabled_short_circuit(snapshot, stub_openrouter):
    """``use_llm_judge=False`` must return early without touching metrics."""
    # Install a stub but expect 0 calls.
    scripted = stub_openrouter([])
    metrics: dict = {"accuracy": 0.5}
    score, model = eval_mod._apply_llm_judge(
        use_llm_judge=False,
        task_type=TaskType.QA,
        judge_model=None,
        settings=_settings_for_judge(),
        questions=["q1"],
        expected=["e1"],
        predicted=["p1"],
        metrics=metrics,
    )
    assert {
        "score": score,
        "model": model,
        "metrics_after": metrics,
        "openrouter_calls": len(scripted.calls),
    } == snapshot


def test_apply_llm_judge_classification_autoskip(snapshot, stub_openrouter):
    """Classification with use_llm_judge=True ⇒ note added, no OpenRouter call."""
    scripted = stub_openrouter([])
    metrics: dict = {"accuracy": 0.92, "f1_macro": 0.91}
    score, model = eval_mod._apply_llm_judge(
        use_llm_judge=True,
        task_type=TaskType.CLASSIFICATION,
        judge_model="explicit/judge",
        settings=_settings_for_judge(),
        questions=["q1"],
        expected=["a"],
        predicted=["a"],
        metrics=metrics,
    )
    assert {
        "score": score,
        "model": model,
        "metrics_after": metrics,
        "openrouter_calls": len(scripted.calls),
    } == snapshot


def test_apply_llm_judge_qa_happy_path(snapshot, stub_openrouter):
    """Every QA row scored 4 ⇒ mean 4.0, judge_model resolved from settings."""
    scripted = stub_openrouter(
        [
            _result('{"score": 4, "reason": "close enough"}'),
            _result('{"score": 4, "reason": "ok"}'),
            _result('{"score": 4, "reason": "fine"}'),
        ]
    )
    metrics: dict = {"exact_match": 0.0, "rouge_l": 0.4}
    score, model = eval_mod._apply_llm_judge(
        use_llm_judge=True,
        task_type=TaskType.QA,
        judge_model=None,  # fall through to settings.llm_judge_model
        settings=_settings_for_judge(),
        questions=["q1", "q2", "q3"],
        expected=["e1", "e2", "e3"],
        predicted=["p1", "p2", "p3"],
        metrics=metrics,
    )
    assert {
        "score": score,
        "model": model,
        "metrics_after": metrics,
        "openrouter_call_count": len(scripted.calls),
        "first_call_kwargs_keys": sorted(scripted.calls[0].keys()),
        "first_call_model": scripted.calls[0].get("model"),
        "first_call_temperature": scripted.calls[0].get("temperature"),
        "first_call_response_format": scripted.calls[0].get("response_format"),
    } == snapshot


def test_apply_llm_judge_tool_calling_happy_path(snapshot, stub_openrouter):
    """Tool-calling task with explicit judge_model override and mixed scores."""
    scripted = stub_openrouter(
        [
            _result('{"score": 5, "reason": "exact"}'),
            _result('{"score": 3, "reason": "wrong param"}'),
        ]
    )
    metrics: dict = {"tool_accuracy": 0.5}
    score, model = eval_mod._apply_llm_judge(
        use_llm_judge=True,
        task_type=TaskType.TOOL_CALLING,
        judge_model="anthropic/claude-judge",
        settings=_settings_for_judge(),
        questions=["q1", "q2"],
        expected=["e1", "e2"],
        predicted=["p1", "p2"],
        metrics=metrics,
    )
    assert {
        "score": score,
        "model": model,
        "metrics_after": metrics,
        "openrouter_call_count": len(scripted.calls),
    } == snapshot


def test_apply_llm_judge_openrouter_error_increments_skipped(snapshot, stub_openrouter):
    """Per-row exception → caught in judge_rows; mean over successful rows only."""
    scripted = stub_openrouter(
        [
            _result('{"score": 5, "reason": "great"}'),
            RuntimeError("OpenRouter 500: upstream"),
            _result('{"score": 3, "reason": "ok"}'),
        ]
    )
    metrics: dict = {"exact_match": 0.0}
    score, model = eval_mod._apply_llm_judge(
        use_llm_judge=True,
        task_type=TaskType.QA,
        judge_model=None,
        settings=_settings_for_judge(),
        questions=["q1", "q2", "q3"],
        expected=["e1", "e2", "e3"],
        predicted=["p1", "p2", "p3"],
        metrics=metrics,
    )
    assert {
        "score": score,
        "model": model,
        "metrics_after": metrics,
        "openrouter_call_count": len(scripted.calls),
    } == snapshot


def test_apply_llm_judge_all_rows_fail_returns_none_mean(snapshot, stub_openrouter):
    """All rows error ⇒ mean_score=None (no signal vs score=0)."""
    scripted = stub_openrouter(
        [
            RuntimeError("OpenRouter 500: upstream"),
            RuntimeError("OpenRouter 500: upstream"),
        ]
    )
    metrics: dict = {"exact_match": 0.0}
    score, model = eval_mod._apply_llm_judge(
        use_llm_judge=True,
        task_type=TaskType.QA,
        judge_model=None,
        settings=_settings_for_judge(),
        questions=["q1", "q2"],
        expected=["e1", "e2"],
        predicted=["p1", "p2"],
        metrics=metrics,
    )
    assert {
        "score": score,
        "model": model,
        "metrics_after": metrics,
        "openrouter_call_count": len(scripted.calls),
    } == snapshot


# ---------------------------------------------------------------------------
# Tier 2 — judge_rows aggregation (no _apply_llm_judge wrapper)
# ---------------------------------------------------------------------------


def test_judge_rows_aggregates_scores_and_skipped(snapshot, stub_openrouter):
    """Direct judge_rows call — snapshots the full JudgeBatchResult shape."""
    scripted = stub_openrouter(
        [
            _result('{"score": 5, "reason": "great"}'),
            RuntimeError("transient parse failure"),
            _result('{"score": 2, "reason": "weak"}'),
            _result('{"score": 4, "reason": "good"}'),
        ]
    )
    client = OpenRouterClient(
        api_key="sk-stub",
        teacher_model="stub-judge",
    )
    batch = judge_rows(
        client=client,
        judge_model="stub-judge",
        questions=["q1", "q2", "q3", "q4"],
        expected=["e1", "e2", "e3", "e4"],
        predicted=["p1", "p2", "p3", "p4"],
    )
    assert {
        "rows": [asdict(r) for r in batch.rows],
        "mean_score": batch.mean_score,
        "judge_model": batch.judge_model,
        "skipped": batch.skipped,
        "openrouter_call_count": len(scripted.calls),
    } == snapshot
