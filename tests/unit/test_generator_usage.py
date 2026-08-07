"""T8 — usage-accumulation + budget-enforcement tests for `SyntheticDataGenerator.generate()`.

Drives the full SDG orchestrator (QA, description_only — no seed, no PDF,
no sentinel branch, keeps the test small) against a mocked OpenRouter
responder via the shared `openrouter_responder` conftest fixture, and
asserts on the `UsageAccumulator` the worker would pass in.

The three OpenRouter model constants (`DIVERSITY_RULES`, `GENERATOR`,
`JUDGE` in `ai_engine/data_gen/models.py`) all currently resolve to the
same literal model string, so the responder can't dispatch on `model`.
It dispatches on `temperature` instead, which *is* distinct per stage in
`generator.py`: meta-prompt uses 0.5, the judge uses 0.0, and the
generator uses the request's `temperature` (default 0.9) — see
`SyntheticDataGenerator._meta_prompt` / `_judge_filter` / `generate()`.
"""

from __future__ import annotations

import json
import random
from types import SimpleNamespace
from typing import Any

import pytest

from ai_engine.data_gen.generator import SyntheticDataGenerator
from ai_engine.data_gen.openrouter_client import AsyncOpenRouterClient, OpenRouterClient
from ai_engine.data_gen.usage import (
    STAGE_GENERATE,
    STAGE_JUDGE,
    STAGE_META_PROMPT,
    SDGBudgetExceededError,
    UsageAccumulator,
)
from api.schemas.enums import TaskType
from api.schemas.sdg import SDGRequestDescriptionOnly

# Per-stage token counts the fake responder returns. Distinct per stage so a
# wrong bucket (e.g. judge tokens landing under "generate") shows up as a
# wrong sum rather than accidentally matching.
META_TOKENS = (101, 55)
GENERATE_TOKENS = (37, 19)
JUDGE_TOKENS = (13, 7)

_MODEL = "deepseek/deepseek-v4-flash-0731"  # what models.py actually resolves to


def _response(content: str, *, prompt_tokens: int, completion_tokens: int) -> Any:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason="stop",
            )
        ],
        model=_MODEL,
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
        ),
    )


def _meta_response_json() -> str:
    return json.dumps(
        {"diversity_rules": [f"rule {i}: vary phrasing" for i in range(1, 11)]}
    )


def _judge_response_json() -> str:
    # weighted = 0.4*0.9 + 0.3*0.9 + 0.3*0.9 = 0.9 >= JUDGE_THRESHOLD (0.7)
    return json.dumps(
        {"fidelity": 0.9, "naturalness": 0.9, "utility": 0.9, "reasoning": "ok"}
    )


def _qa_pool() -> list[dict[str, str]]:
    return [
        {"question": f"question #{i:02d}?", "answer": f"answer {i:02d}."}
        for i in range(50)
    ]


class _StageDispatchResponder:
    """Dispatch by `temperature` (see module docstring for why not `model`).

    Tracks how many calls landed in each bucket so the test can compute the
    expected accumulator sums independently of internal batch-size math
    (`CANDIDATES_PER_GEN_CALL` / over-gen multiplier / etc.) — the test only
    needs to know "N calls happened for stage X", not predict N itself.
    """

    def __init__(self) -> None:
        self.meta_calls = 0
        self.generate_calls = 0
        self.judge_calls = 0
        self._pool = _qa_pool()
        self._gen_idx = 0

    async def __call__(self, kwargs: dict[str, Any], call_index: int) -> Any:
        temperature = kwargs.get("temperature")
        if temperature == 0.5:
            self.meta_calls += 1
            return _response(
                _meta_response_json(),
                prompt_tokens=META_TOKENS[0],
                completion_tokens=META_TOKENS[1],
            )
        if temperature == 0.0:
            self.judge_calls += 1
            return _response(
                _judge_response_json(),
                prompt_tokens=JUDGE_TOKENS[0],
                completion_tokens=JUDGE_TOKENS[1],
            )
        # Generator call (request.temperature, default 0.9).
        self.generate_calls += 1
        start = (self._gen_idx * 5) % (len(self._pool) - 5)
        slice_ = self._pool[start : start + 5]
        self._gen_idx += 1
        return _response(
            json.dumps({"samples": slice_}, ensure_ascii=False),
            prompt_tokens=GENERATE_TOKENS[0],
            completion_tokens=GENERATE_TOKENS[1],
        )


def _make_request(**overrides: Any) -> SDGRequestDescriptionOnly:
    kwargs: dict[str, Any] = dict(
        project_id="00000000-0000-0000-0000-000000000040",
        task_type=TaskType.QA,
        task_description="ตอบคำถามนโยบายการคืนสินค้า 30 วัน",
        num_samples=4,
        holdout_size=0,
    )
    kwargs.update(overrides)
    return SDGRequestDescriptionOnly(**kwargs)


async def _run(
    request: SDGRequestDescriptionOnly,
    *,
    openrouter_responder,
    usage: UsageAccumulator | None,
    rng_seed: int = 0,
) -> tuple[Any, _StageDispatchResponder]:
    async_client = AsyncOpenRouterClient(api_key="dummy")
    sync_client = OpenRouterClient(api_key="dummy", teacher_model="placeholder/unused")
    responder = _StageDispatchResponder()
    openrouter_responder(async_client, responder)
    # No PDF branch exercised (pdf_bytes=None), but hand the generator a
    # working stub sync client anyway (matches test_snapshot_node_3b4.py).
    sync_client._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: None))
    )
    gen = SyntheticDataGenerator(async_client, sync_client, rng=random.Random(rng_seed))
    try:
        result = await gen.generate(
            request, seed_rows=[], pdf_bytes=None, usage=usage
        )
    finally:
        await async_client.aclose()
    return result, responder


# ---------------------------------------------------------------------------
# 1. Accumulation across meta_prompt / generate / judge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_accumulates_usage_per_stage(openrouter_responder):
    request = _make_request()
    usage = UsageAccumulator()

    result, responder = await _run(
        request, openrouter_responder=openrouter_responder, usage=usage
    )

    # Sanity: the run actually produced rows and exercised all three stages.
    assert result.valid_rows
    assert responder.meta_calls == 1
    assert responder.generate_calls > 0
    assert responder.judge_calls > 0

    entries = {(e.model, e.stage): e for e in usage.entries()}

    assert (_MODEL, STAGE_META_PROMPT) in entries
    meta_entry = entries[(_MODEL, STAGE_META_PROMPT)]
    assert meta_entry.prompt_tokens == META_TOKENS[0] * responder.meta_calls
    assert meta_entry.completion_tokens == META_TOKENS[1] * responder.meta_calls

    assert (_MODEL, STAGE_GENERATE) in entries
    gen_entry = entries[(_MODEL, STAGE_GENERATE)]
    assert gen_entry.prompt_tokens == GENERATE_TOKENS[0] * responder.generate_calls
    assert gen_entry.completion_tokens == GENERATE_TOKENS[1] * responder.generate_calls

    assert (_MODEL, STAGE_JUDGE) in entries
    judge_entry = entries[(_MODEL, STAGE_JUDGE)]
    assert judge_entry.prompt_tokens == JUDGE_TOKENS[0] * responder.judge_calls
    assert judge_entry.completion_tokens == JUDGE_TOKENS[1] * responder.judge_calls


# ---------------------------------------------------------------------------
# 2. Budget enforcement propagates out of generate()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_propagates_budget_exceeded(openrouter_responder):
    """A tiny budget blown mid-run raises SDGBudgetExceededError out of
    generate() rather than being swallowed by any broad `except Exception`
    in the SDG loop (the generator batch's own failure handler, the judge's
    wholesale-failure handler, and the PDF best-effort handler all wrap only
    their own chat_batch()/chat() call, not the usage.check_budget() call
    sites — see generator.py).
    """
    request = _make_request()
    # A single meta-prompt call alone (101 prompt + 55 completion tokens,
    # priced below) already exceeds this budget, but check_budget() is only
    # invoked after batch stages per the spec — so it takes at least a
    # generator batch call to trip it. Either way it must raise before
    # generate() returns.
    prices = {_MODEL: (1.0, 1.0)}  # $1/token — trivially exceeded almost immediately
    usage = UsageAccumulator(prices=prices, budget_remaining_usd=0.01)

    with pytest.raises(SDGBudgetExceededError):
        await _run(request, openrouter_responder=openrouter_responder, usage=usage)


# ---------------------------------------------------------------------------
# 3. usage=None (the default) changes nothing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_usage_none_is_untouched(openrouter_responder):
    """The default `usage=None` must not change generate()'s behaviour or
    outcome at all — every existing caller (worker code not yet updated,
    every snapshot test) keeps working byte-for-byte."""
    request = _make_request()

    result_without, _ = await _run(
        request, openrouter_responder=openrouter_responder, usage=None, rng_seed=99
    )
    result_with, _ = await _run(
        request,
        openrouter_responder=openrouter_responder,
        usage=UsageAccumulator(),
        rng_seed=99,
    )

    # Same RNG seed + same mocked responses ⇒ identical SDGRunResult, with
    # or without an accumulator wired in.
    assert result_without.valid_rows == result_with.valid_rows
    assert result_without.rejected_count == result_with.rejected_count
    assert result_without.duplicate_count == result_with.duplicate_count
    assert result_without.judge_rejected_count == result_with.judge_rejected_count
    assert result_without.judge_parse_failures == result_with.judge_parse_failures
    assert result_without.api_calls == result_with.api_calls
    assert result_without.failed_attempts == result_with.failed_attempts
