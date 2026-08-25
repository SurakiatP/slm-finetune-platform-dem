"""T6 — dedup-before-judge ordering + batched judging + judge-score exposure.

Covers the three SDG-loop changes made in `ai_engine/data_gen/generator.py`:

  1. MinHash dedup now runs BEFORE the judge gate (`generate()`'s loop
     order), so a duplicate candidate never spends a judge call.
  2. `_judge_filter` batches rows into `JUDGE_ROWS_PER_CALL`-sized chunks
     per quota-key group and parses each chunk with
     `parse_judge_batch_response`, instead of one judge call per row.
  3. `_judge_filter` records every successfully parsed score (kept or
     rejected) onto a shared `JudgeScoreAggregator`, and `generate()`
     surfaces the rollup on `SDGRunResult.judge_scores_summary`.

Tests (a) and (b) drive `SyntheticDataGenerator._judge_filter` directly —
it is the unit under test for batching/parse-failure behaviour and this
gives byte-exact control over chunk sizes and judge responses that a full
`generate()` run cannot offer without fighting the quota/coverage-pool
machinery. Tests (c) and (d) drive the full `generate()` loop (same
`openrouter_responder` fixture pattern as `test_generator_usage.py`) since
they assert on cross-stage behaviour (dedup happening before the judge
ever sees a row; the aggregator being threaded through the whole loop and
returned on the final result).
"""

from __future__ import annotations

import json
import random
from types import SimpleNamespace
from typing import Any

import pytest

from ai_engine.data_gen.constants import JUDGE_ROWS_PER_CALL
from ai_engine.data_gen.generator import SyntheticDataGenerator
from ai_engine.data_gen.insights import JudgeScoreAggregator
from ai_engine.data_gen.openrouter_client import AsyncOpenRouterClient, OpenRouterClient
from api.schemas.enums import TaskType
from api.schemas.sdg import SDGRequestDescriptionOnly


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _build_response(
    content: str, *, model: str = "stub", prompt_tokens: int = 10, completion_tokens: int = 5
) -> Any:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")
        ],
        model=model,
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


def _judge_score_entry(index: int, *, high: bool = True) -> dict[str, Any]:
    if high:
        return {
            "index": index,
            "reasoning": "ok",
            "fidelity": 0.9,
            "naturalness": 0.9,
            "utility": 0.9,
        }
    return {
        "index": index,
        "reasoning": "bad",
        "fidelity": 0.1,
        "naturalness": 0.1,
        "utility": 0.1,
    }


def _extract_judge_row_count(kwargs: dict[str, Any]) -> int:
    """How many rows `build_judge_prompt` embedded in this call's user msg.

    Mirrors `build_judge_prompt`'s `[Rows to evaluate]\\n<json list>` block.
    """
    messages = kwargs.get("messages") or []
    user_msg = ""
    for m in messages:
        if m.get("role") == "user":
            user_msg = m.get("content", "")
            break
    marker = "[Rows to evaluate]\n"
    idx = user_msg.find(marker)
    if idx == -1:
        return 0
    rest = user_msg[idx + len(marker) :]
    end = rest.find("\n\n[")
    payload = rest if end == -1 else rest[:end]
    try:
        rows = json.loads(payload)
    except (ValueError, TypeError):
        return 0
    return len(rows) if isinstance(rows, list) else 0


def _make_generator(*, rng_seed: int = 0) -> tuple[SyntheticDataGenerator, AsyncOpenRouterClient]:
    async_client = AsyncOpenRouterClient(api_key="dummy")
    sync_client = OpenRouterClient(api_key="dummy", teacher_model="placeholder/unused")
    gen = SyntheticDataGenerator(async_client, sync_client, rng=random.Random(rng_seed))
    return gen, async_client


def _make_request(**overrides: Any) -> SDGRequestDescriptionOnly:
    kwargs: dict[str, Any] = dict(
        project_id="00000000-0000-0000-0000-000000000060",
        task_type=TaskType.QA,
        task_description="Answer questions about a 30-day return policy",
        num_samples=3,
        holdout_size=0,
    )
    kwargs.update(overrides)
    return SDGRequestDescriptionOnly(**kwargs)


# ---------------------------------------------------------------------------
# (a) 25 same-key rows, JUDGE_ROWS_PER_CALL=10 -> <=3 judge calls, all scored
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_judge_filter_batches_same_key_rows_into_few_calls(openrouter_responder):
    gen, async_client = _make_generator()
    request = _make_request()
    rows = [{"question": f"q{i}", "answer": f"a{i}"} for i in range(25)]

    async def responder(kwargs: dict[str, Any], call_index: int) -> Any:
        n = _extract_judge_row_count(kwargs)
        scores = {"scores": [_judge_score_entry(i) for i in range(n)]}
        return _build_response(json.dumps(scores))

    openrouter_responder(async_client, responder)
    aggregator = JudgeScoreAggregator()
    try:
        kept, low, parse_fail, api_calls = await gen._judge_filter(
            request=request,
            rows=rows,
            classification_labels=None,
            tool_definitions=None,
            usage=None,
            aggregator=aggregator,
        )
    finally:
        await async_client.aclose()

    # 25 rows / JUDGE_ROWS_PER_CALL(10) per chunk -> chunks of 10, 10, 5.
    assert JUDGE_ROWS_PER_CALL == 10
    assert api_calls == 3
    assert low == 0
    assert parse_fail == 0
    assert len(kept) == 25
    # Every row was scored and recorded, kept or not.
    assert len(aggregator) == 25


# ---------------------------------------------------------------------------
# (b) a chunk response omitting one index -> exactly one parse failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_judge_filter_missing_index_counts_one_parse_failure(openrouter_responder):
    gen, async_client = _make_generator()
    request = _make_request()
    # Exactly one chunk (10 rows == JUDGE_ROWS_PER_CALL).
    rows = [{"question": f"q{i}", "answer": f"a{i}"} for i in range(JUDGE_ROWS_PER_CALL)]

    async def responder(kwargs: dict[str, Any], call_index: int) -> Any:
        n = _extract_judge_row_count(kwargs)
        # Omit index 9 (the last row of the chunk) from the response.
        scores = {"scores": [_judge_score_entry(i) for i in range(n) if i != 9]}
        return _build_response(json.dumps(scores))

    openrouter_responder(async_client, responder)
    aggregator = JudgeScoreAggregator()
    try:
        kept, low, parse_fail, api_calls = await gen._judge_filter(
            request=request,
            rows=rows,
            classification_labels=None,
            tool_definitions=None,
            usage=None,
            aggregator=aggregator,
        )
    finally:
        await async_client.aclose()

    assert api_calls == 1
    assert parse_fail == 1
    assert low == 0
    # The other 9 rows all scored high enough to survive the gate.
    assert len(kept) == 9
    assert len(aggregator) == 9


# ---------------------------------------------------------------------------
# (c) duplicates are dropped BEFORE the judge sees them
# ---------------------------------------------------------------------------


class _DedupOrderResponder:
    """Dispatches by `temperature` (meta=0.5, judge=0.0, generator=request's).

    The generator call returns 5 QA rows where two share an identical
    `question` (an exact duplicate) — MinHash dedup should collapse this to
    4 unique rows *before* the judge is ever invoked. Every judge call's
    embedded row count is recorded so the test can assert the judge only
    ever saw the deduped population.
    """

    def __init__(self) -> None:
        self.gen_calls = 0
        self.judge_row_counts: list[int] = []

    async def __call__(self, kwargs: dict[str, Any], call_index: int) -> Any:
        temperature = kwargs.get("temperature")
        if temperature == 0.5:
            return _build_response(
                json.dumps({"diversity_rules": [f"rule {i}: vary phrasing" for i in range(1, 9)]})
            )
        if temperature == 0.0:
            n = _extract_judge_row_count(kwargs)
            self.judge_row_counts.append(n)
            scores = {"scores": [_judge_score_entry(i) for i in range(n)]}
            return _build_response(json.dumps(scores))
        # Generator call (request.temperature, default 0.9).
        self.gen_calls += 1
        rows = [
            {"question": "duplicate question text", "answer": "answer A"},
            {"question": "duplicate question text", "answer": "answer A"},  # exact dup
            {"question": "unique question two", "answer": "answer B"},
            {"question": "unique question three", "answer": "answer C"},
            {"question": "unique question four", "answer": "answer D"},
        ]
        return _build_response(json.dumps({"samples": rows}))


async def _run_qa(request, *, openrouter_responder, rng_seed: int = 0):
    async_client = AsyncOpenRouterClient(api_key="dummy")
    sync_client = OpenRouterClient(api_key="dummy", teacher_model="placeholder/unused")
    responder = _DedupOrderResponder()
    openrouter_responder(async_client, responder)
    sync_client._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: None))
    )
    gen = SyntheticDataGenerator(async_client, sync_client, rng=random.Random(rng_seed))
    try:
        result = await gen.generate(request, seed_rows=[], pdf_bytes=None)
    finally:
        await async_client.aclose()
    return result, responder


@pytest.mark.asyncio
async def test_generate_dedups_before_judge_sees_duplicates(openrouter_responder):
    request = _make_request(num_samples=3)

    result, responder = await _run_qa(request, openrouter_responder=openrouter_responder)

    assert responder.gen_calls >= 1
    # The judge must only ever have been shown the deduped row count: the
    # generator emitted 5 rows containing 1 exact duplicate -> 4 unique.
    assert sum(responder.judge_row_counts) == 4
    assert result.duplicate_count == 1


# ---------------------------------------------------------------------------
# (d) judge_scores_summary populated with count/mean/histogram/by_key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_exposes_judge_scores_summary(openrouter_responder):
    request = _make_request(num_samples=3)

    result, _responder = await _run_qa(request, openrouter_responder=openrouter_responder)

    summary = result.judge_scores_summary
    assert summary is not None
    assert summary["count"] > 0
    for axis in ("fidelity", "naturalness", "utility", "weighted"):
        assert axis in summary["mean"]
        assert len(summary["histogram"][axis]) == 10
    assert "__qa__" in summary["by_key"]
    assert summary["by_key"]["__qa__"]["count"] == summary["count"]
