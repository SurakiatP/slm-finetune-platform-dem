"""W2-T6 — semantic (embedding) dedup layer wiring in
`SyntheticDataGenerator.generate()`.

Modeled on `test_generator_usage.py`'s `_run` helper (same
`openrouter_responder` fixture, `SDGRequestDescriptionOnly`, QA task,
`random.Random(seed)`, stubbed sync client). Drives the full orchestrator
against a mocked OpenRouter responder, plus the `openrouter_responder`
fixture's new `embeddings_responder` keyword (see `tests/conftest.py`) to
program the fake `AsyncOpenAI.embeddings.create` the semantic layer's
`_embed` closure calls through `AsyncOpenRouterClient.embed()`.

Covers, per the task spec:

  1. disabled path — `embedding_model=None` (the default) => zero
     embeddings calls, `semantic_duplicate_count == 0`, run succeeds.
  2. ordering — identical-vector embeddings collapse a validated batch to
     one survivor, so the judge sees fewer rows than validation produced,
     and every embeddings call happens before its corresponding judge call
     (shared recorded call log).
  3. counting — `semantic_duplicate_count > 0` and `duplicate_count`
     (the combined MinHash + semantic tally) includes it.
  4. degrade — an `APIConnectionError` from the embeddings endpoint
     degrades the semantic layer to a permanent pass-through; the run
     still completes, `semantic_duplicate_count` stays 0, rows are still
     collected, no exception escapes.
  5. usage + budget — a priced embedding model with a tiny remaining
     budget makes `SDGBudgetExceededError` propagate out of `generate()`
     from the embed stage; with an unlimited budget, `usage.entries()`
     gets a `(model, "embed")` bucket with `completion_tokens == 0`
     (embeddings bill prompt tokens only).
"""

from __future__ import annotations

import json
import random
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import APIConnectionError

from ai_engine.data_gen.generator import SyntheticDataGenerator
from ai_engine.data_gen.openrouter_client import AsyncOpenRouterClient, OpenRouterClient
from ai_engine.data_gen.usage import (
    STAGE_EMBED,
    SDGBudgetExceededError,
    UsageAccumulator,
)
from api.schemas.enums import TaskType
from api.schemas.sdg import SDGRequestDescriptionOnly

# Per-stage token counts the fake chat responder returns — distinct so a
# wrong bucket shows up as a wrong sum rather than accidentally matching.
META_TOKENS = (101, 55)
GENERATE_TOKENS = (37, 19)
JUDGE_TOKENS = (13, 7)
EMBED_PROMPT_TOKENS_PER_TEXT = 4

_MODEL = "deepseek/deepseek-v4-flash-0731"  # what models.py actually resolves to
_EMBED_MODEL = "stub/embedding-3-small"  # arbitrary stub embedding model id


# ---------------------------------------------------------------------------
# Fixtures: fast retries (embed() retries on APIConnectionError, tenacity
# would otherwise really sleep 2/4/8s per exhausted attempt sequence)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Eliminate tenacity's real sleeping — same pattern as
    `test_embeddings_client.py`."""
    import ai_engine.data_gen.openrouter_client as mod

    monkeypatch.setattr(mod, "wait_exponential", lambda **_kw: (lambda _retry_state: 0))


# ---------------------------------------------------------------------------
# Shared chat responder (meta / generate / judge dispatch by temperature)
# ---------------------------------------------------------------------------


class _CallLog:
    """Ordering witness shared between the chat responder and the
    embeddings responder — records tags in the order calls actually land."""

    def __init__(self) -> None:
        self.entries: list[str] = []

    def record(self, tag: str) -> None:
        self.entries.append(tag)


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


def _judge_response_json(num_rows: int = 1) -> str:
    # weighted = 0.4*0.9 + 0.3*0.9 + 0.3*0.9 = 0.9 >= JUDGE_THRESHOLD (0.7)
    return json.dumps(
        {
            "scores": [
                {
                    "index": i,
                    "reasoning": "ok",
                    "fidelity": 0.9,
                    "naturalness": 0.9,
                    "utility": 0.9,
                }
                for i in range(num_rows)
            ]
        }
    )


def _count_judge_rows(kwargs: dict[str, Any]) -> int:
    """Extract how many rows the judge prompt asked to score (mirrors
    `test_generator_usage.py`'s helper of the same name)."""
    messages = kwargs.get("messages") or []
    user_msg = ""
    for m in messages:
        if m.get("role") == "user":
            user_msg = m.get("content", "")
            break
    marker = "[Rows to evaluate]\n"
    idx = user_msg.find(marker)
    if idx == -1:
        return 1
    rest = user_msg[idx + len(marker) :]
    end = rest.find("\n\n[")
    payload = rest if end == -1 else rest[:end]
    try:
        rows = json.loads(payload)
    except (ValueError, TypeError):
        return 1
    return len(rows) if isinstance(rows, list) else 1


def _qa_pool() -> list[dict[str, str]]:
    return [
        {"question": f"question #{i:02d}?", "answer": f"answer {i:02d}."}
        for i in range(50)
    ]


class _StageDispatchResponder:
    """Dispatch by `temperature` (meta=0.5, judge=0.0, generator=request's).

    Optionally tags every judge call onto a shared `_CallLog` so a test can
    assert embeddings calls land before their corresponding judge calls.
    """

    def __init__(self, *, call_log: _CallLog | None = None) -> None:
        self.meta_calls = 0
        self.generate_calls = 0
        self.judge_calls = 0
        self.judge_row_counts: list[int] = []
        self._pool = _qa_pool()
        self._gen_idx = 0
        self._call_log = call_log

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
            if self._call_log is not None:
                self._call_log.record("judge")
            num_rows = _count_judge_rows(kwargs)
            self.judge_row_counts.append(num_rows)
            return _response(
                _judge_response_json(num_rows),
                prompt_tokens=JUDGE_TOKENS[0],
                completion_tokens=JUDGE_TOKENS[1],
            )
        # Generator call (request.temperature, default 0.9).
        self.generate_calls += 1
        denom = max(1, len(self._pool) - 5)
        start = (self._gen_idx * 5) % denom
        slice_ = self._pool[start : start + 5]
        self._gen_idx += 1
        return _response(
            json.dumps({"samples": slice_}, ensure_ascii=False),
            prompt_tokens=GENERATE_TOKENS[0],
            completion_tokens=GENERATE_TOKENS[1],
        )


def _make_request(**overrides: Any) -> SDGRequestDescriptionOnly:
    kwargs: dict[str, Any] = dict(
        project_id="00000000-0000-0000-0000-000000000070",
        task_type=TaskType.QA,
        task_description="ตอบคำถามนโยบายการคืนสินค้า 30 วัน",
        num_samples=4,
        holdout_size=0,
    )
    kwargs.update(overrides)
    return SDGRequestDescriptionOnly(**kwargs)


# ---------------------------------------------------------------------------
# Embeddings responders
# ---------------------------------------------------------------------------


def _identical_vector_embeddings_responder(
    openrouter_responder, *, call_log: _CallLog | None = None
):
    """Every text embeds to the exact same vector — after the first row in
    a `filter()` call, every subsequent row (same call or later) reads as a
    near-duplicate (cosine similarity 1.0 >= threshold)."""

    async def _responder(kwargs: dict[str, Any], call_index: int) -> Any:
        if call_log is not None:
            call_log.record("embed")
        texts = kwargs.get("input") or []
        model = kwargs.get("model", _EMBED_MODEL)
        vectors = [[1.0, 0.0, 0.0] for _ in texts]
        return openrouter_responder.build_embeddings_response(
            vectors,
            model=model,
            prompt_tokens=EMBED_PROMPT_TOKENS_PER_TEXT * len(texts),
        )

    return _responder


def _varied_vector_embeddings_responder(openrouter_responder):
    """Distinct-enough vectors that nothing dedups — used where the test
    only cares about usage accounting, not dedup counts."""

    async def _responder(kwargs: dict[str, Any], call_index: int) -> Any:
        texts = kwargs.get("input") or []
        model = kwargs.get("model", _EMBED_MODEL)
        vectors = [[float(i + 1), 0.0, 0.0] for i, _ in enumerate(texts)]
        return openrouter_responder.build_embeddings_response(
            vectors,
            model=model,
            prompt_tokens=EMBED_PROMPT_TOKENS_PER_TEXT * len(texts),
        )

    return _responder


async def _always_fail_embeddings_responder(kwargs: dict[str, Any], call_index: int) -> Any:
    raise APIConnectionError(
        request=httpx.Request("POST", "https://openrouter.ai/api/v1/embeddings")
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def _run(
    request: SDGRequestDescriptionOnly,
    *,
    openrouter_responder,
    usage: UsageAccumulator | None = None,
    rng_seed: int = 0,
    embedding_model: str | None = None,
    embeddings_responder: Any = None,
    call_log: _CallLog | None = None,
) -> tuple[Any, _StageDispatchResponder, Any]:
    async_client = AsyncOpenRouterClient(api_key="dummy")
    sync_client = OpenRouterClient(api_key="dummy", teacher_model="placeholder/unused")
    responder = _StageDispatchResponder(call_log=call_log)
    fake = openrouter_responder(
        async_client, responder, embeddings_responder=embeddings_responder
    )
    # No PDF branch exercised (pdf_bytes=None), but hand the generator a
    # working stub sync client anyway (matches test_generator_usage.py).
    sync_client._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: None))
    )
    gen = SyntheticDataGenerator(
        async_client,
        sync_client,
        rng=random.Random(rng_seed),
        embedding_model=embedding_model,
    )
    try:
        result = await gen.generate(
            request, seed_rows=[], pdf_bytes=None, usage=usage
        )
    finally:
        await async_client.aclose()
    return result, responder, fake


# ---------------------------------------------------------------------------
# 1. Disabled path — embedding_model=None (default)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_semantic_layer_disabled_by_default(openrouter_responder):
    request = _make_request()

    result, responder, fake = await _run(
        request, openrouter_responder=openrouter_responder, embedding_model=None
    )

    assert result.valid_rows
    assert result.semantic_duplicate_count == 0
    assert fake.embeddings.calls == []  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 2 + 3. Ordering + counting — identical embeddings collapse a batch,
#         judge sees fewer rows than validation produced, embed precedes
#         its corresponding judge call, semantic_duplicate_count folds
#         into duplicate_count.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_semantic_dedup_drops_near_duplicates_before_judge_sees_them(
    openrouter_responder,
):
    # num_samples=1 so the run finishes inside a single loop iteration —
    # one generator call (5 candidate rows, all distinct text so MinHash
    # lets every one through), one embeddings call, one judge call. Keeps
    # the ordering/counting assertions unambiguous without racing the
    # consecutive-zero-yield abort (a target requiring a second loop would
    # find the semantic layer's own stored vector already saturating every
    # later row, yielding zero collected rows and tripping
    # MAX_CONSECUTIVE_FAILURES — out of scope for what this test checks).
    request = _make_request(num_samples=1)
    call_log = _CallLog()
    embeddings_responder = _identical_vector_embeddings_responder(
        openrouter_responder, call_log=call_log
    )

    result, responder, fake = await _run(
        request,
        openrouter_responder=openrouter_responder,
        embedding_model=_EMBED_MODEL,
        embeddings_responder=embeddings_responder,
        call_log=call_log,
    )

    validated_rows_total = responder.generate_calls * 5
    judged_rows_total = sum(responder.judge_row_counts)

    # counting
    assert result.semantic_duplicate_count > 0
    assert result.duplicate_count >= result.semantic_duplicate_count

    # ordering: the judge saw fewer rows than validation produced, and the
    # first embeddings call happened before the first judge call.
    assert judged_rows_total < validated_rows_total
    assert "embed" in call_log.entries
    assert "judge" in call_log.entries
    assert call_log.entries.index("embed") < call_log.entries.index("judge")


# ---------------------------------------------------------------------------
# 4. Degrade — embeddings outage doesn't fail the run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_semantic_dedup_degrades_on_embeddings_failure(openrouter_responder):
    request = _make_request()

    result, responder, fake = await _run(
        request,
        openrouter_responder=openrouter_responder,
        embedding_model=_EMBED_MODEL,
        embeddings_responder=_always_fail_embeddings_responder,
    )

    assert result.valid_rows  # rows still collected
    assert result.semantic_duplicate_count == 0
    assert fake.embeddings.calls  # the embeddings endpoint was in fact tried


# ---------------------------------------------------------------------------
# 5. Usage + budget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_propagates_budget_exceeded_from_embed_stage(
    openrouter_responder,
):
    request = _make_request(num_samples=1)
    embeddings_responder = _varied_vector_embeddings_responder(openrouter_responder)
    # Priced heavily enough that a single embeddings call already blows the
    # budget; unlike the judge/generate stages, check_budget() here is
    # invoked from the SemanticDeduplicator's on_usage callback, which is
    # deliberately left outside its own degrade-on-error try/except (see
    # ai_engine/data_gen/semantic_dedup.py), so the breach must propagate
    # straight out of generate() rather than degrade the layer.
    prices = {_EMBED_MODEL: (1.0, 1.0)}
    usage = UsageAccumulator(prices=prices, budget_remaining_usd=0.0001)

    with pytest.raises(SDGBudgetExceededError):
        await _run(
            request,
            openrouter_responder=openrouter_responder,
            embedding_model=_EMBED_MODEL,
            embeddings_responder=embeddings_responder,
            usage=usage,
        )


@pytest.mark.asyncio
async def test_generate_records_embed_usage_bucket_with_zero_completion_tokens(
    openrouter_responder,
):
    request = _make_request(num_samples=1)
    embeddings_responder = _varied_vector_embeddings_responder(openrouter_responder)
    usage = UsageAccumulator()  # unlimited budget

    result, responder, fake = await _run(
        request,
        openrouter_responder=openrouter_responder,
        embedding_model=_EMBED_MODEL,
        embeddings_responder=embeddings_responder,
        usage=usage,
    )

    assert result.valid_rows
    entries = {(e.model, e.stage): e for e in usage.entries()}
    assert (_EMBED_MODEL, STAGE_EMBED) in entries
    embed_entry = entries[(_EMBED_MODEL, STAGE_EMBED)]
    assert embed_entry.prompt_tokens > 0
    assert embed_entry.completion_tokens == 0


# ---------------------------------------------------------------------------
# 6. Seed preload + PDF preload (reviewer-added — these two `add_texts`
#    call sites in `generate()` had no generator-level coverage).
# ---------------------------------------------------------------------------


def _one_hot_embeddings_responder(openrouter_responder, *, aliases: dict[str, str] | None = None):
    """Give every distinct text its own orthogonal one-hot vector, so nothing
    dedups by accident.

    `aliases` maps a text onto another text's slot, which is how a test makes
    exactly one pair collide (cosine 1.0) without touching any other row.
    Note this is genuinely orthogonal, unlike
    `_varied_vector_embeddings_responder`, whose `[i+1, 0, 0]` vectors all
    L2-normalize to the same `[1, 0, 0]`.
    """
    aliases = aliases or {}
    slots: dict[str, int] = {}
    dim = 64

    def _vector(text: str) -> list[float]:
        canonical = aliases.get(text, text)
        if canonical not in slots:
            slots[canonical] = len(slots)
        idx = slots[canonical]
        assert idx < dim, "one-hot responder ran out of dimensions"
        return [1.0 if j == idx else 0.0 for j in range(dim)]

    async def _responder(kwargs: dict[str, Any], call_index: int) -> Any:
        texts = kwargs.get("input") or []
        model = kwargs.get("model", _EMBED_MODEL)
        return openrouter_responder.build_embeddings_response(
            [_vector(t) for t in texts],
            model=model,
            prompt_tokens=EMBED_PROMPT_TOKENS_PER_TEXT * len(texts),
        )

    return _responder


async def _run_with(
    request: SDGRequestDescriptionOnly,
    *,
    openrouter_responder,
    seed_rows: list[dict[str, Any]] | None = None,
    pdf_bytes: bytes | None = None,
    usage: UsageAccumulator | None = None,
    embedding_model: str | None = None,
    embeddings_responder: Any = None,
    pdf_first_pass: Any = None,
    capture: dict[str, Any] | None = None,
) -> tuple[Any, _StageDispatchResponder, Any]:
    """Like `_run`, but able to pass `seed_rows` / `pdf_bytes` (which `_run`
    hardcodes to `[]` / `None`) and to stub `_pdf_first_pass`.

    `capture` (if given) receives the responder/fake *before* `generate()` is
    called, so a test asserting on a raising run can still inspect how far the
    pipeline got.
    """
    async_client = AsyncOpenRouterClient(api_key="dummy")
    sync_client = OpenRouterClient(api_key="dummy", teacher_model="placeholder/unused")
    responder = _StageDispatchResponder()
    fake = openrouter_responder(
        async_client, responder, embeddings_responder=embeddings_responder
    )
    sync_client._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: None))
    )
    gen = SyntheticDataGenerator(
        async_client,
        sync_client,
        rng=random.Random(0),
        embedding_model=embedding_model,
    )
    if pdf_first_pass is not None:
        gen._pdf_first_pass = pdf_first_pass  # type: ignore[method-assign]
    if capture is not None:
        capture["responder"] = responder
        capture["fake"] = fake
    try:
        result = await gen.generate(
            request, seed_rows=seed_rows or [], pdf_bytes=pdf_bytes, usage=usage
        )
    finally:
        await async_client.aclose()
    return result, responder, fake


@pytest.mark.asyncio
async def test_seed_rows_are_pre_embedded_and_empty_seed_text_is_skipped(
    openrouter_responder,
):
    """Seeds must be embedded into the matrix *before* the loop, and only the
    non-empty ones — otherwise a generated row that means the same thing as a
    seed would sail past the semantic layer."""
    seed_question = "How do I return an item I bought last week?"
    seed_rows = [
        {"question": seed_question, "answer": "Within 30 days."},
        {"question": "", "answer": "no question text at all"},
    ]
    # `question #00?` is the first row the generator responder emits; alias it
    # onto the seed's slot so it embeds identically (cosine 1.0) while staying
    # lexically distinct enough that MinHash lets it through.
    embeddings_responder = _one_hot_embeddings_responder(
        openrouter_responder, aliases={"question #00?": seed_question}
    )

    result, responder, fake = await _run_with(
        _make_request(num_samples=1),
        openrouter_responder=openrouter_responder,
        seed_rows=seed_rows,
        embedding_model=_EMBED_MODEL,
        embeddings_responder=embeddings_responder,
    )

    calls = fake.embeddings.calls  # type: ignore[attr-defined]
    assert calls, "seed preload should have issued an embeddings call"
    # First call is the seed preload: exactly the non-empty seed questions.
    assert calls[0]["input"] == [seed_question]
    assert calls[0]["model"] == _EMBED_MODEL

    # The seed twin was dropped by the semantic layer, not by MinHash.
    assert result.semantic_duplicate_count >= 1
    assert result.duplicate_count >= result.semantic_duplicate_count
    assert result.valid_rows
    assert all(r["question"] != "question #00?" for r in result.valid_rows)


@pytest.mark.asyncio
async def test_pdf_derived_rows_are_pre_embedded(openrouter_responder):
    """PDF-derived Q&As go straight into `accepted` without passing through the
    loop's semantic filter, so they must be seeded into the matrix explicitly —
    otherwise the loop could re-generate one of them and not notice."""
    pdf_rows = [
        {"question": "What is the warranty period?", "answer": "Two years."},
        {"question": "Who pays return shipping?", "answer": "We do."},
    ]

    async def _fake_pdf_first_pass(**kwargs: Any) -> tuple[list[dict[str, Any]], int]:
        return list(pdf_rows), 1

    result, responder, fake = await _run_with(
        _make_request(num_samples=3),
        openrouter_responder=openrouter_responder,
        pdf_bytes=b"%PDF-1.4 fake",
        embedding_model=_EMBED_MODEL,
        embeddings_responder=_one_hot_embeddings_responder(openrouter_responder),
        pdf_first_pass=_fake_pdf_first_pass,
    )

    calls = fake.embeddings.calls  # type: ignore[attr-defined]
    assert calls, "PDF preload should have issued an embeddings call"
    # seed_rows=[] means add_texts([]) short-circuits, so the PDF preload is
    # the very first embeddings call of the run.
    assert calls[0]["input"] == [r["question"] for r in pdf_rows]
    assert result.valid_rows


@pytest.mark.asyncio
async def test_budget_breach_during_pdf_preload_is_not_swallowed_by_best_effort_except(
    openrouter_responder,
):
    """The PDF block's broad `except Exception` (PDF is best-effort) must not
    eat an `SDGBudgetExceededError` raised by the semantic layer's `on_usage`
    budget check — the explicit `except SDGBudgetExceededError: raise` ahead of
    it is what keeps that true."""
    pdf_rows = [{"question": "What is the warranty period?", "answer": "Two years."}]

    async def _fake_pdf_first_pass(**kwargs: Any) -> tuple[list[dict[str, Any]], int]:
        return list(pdf_rows), 1

    prices = {_EMBED_MODEL: (1.0, 1.0)}
    usage = UsageAccumulator(prices=prices, budget_remaining_usd=0.0001)
    capture: dict[str, Any] = {}

    with pytest.raises(SDGBudgetExceededError):
        await _run_with(
            _make_request(num_samples=3),
            openrouter_responder=openrouter_responder,
            pdf_bytes=b"%PDF-1.4 fake",
            usage=usage,
            embedding_model=_EMBED_MODEL,
            embeddings_responder=_one_hot_embeddings_responder(openrouter_responder),
            pdf_first_pass=_fake_pdf_first_pass,
            capture=capture,
        )

    # Load-bearing: asserting only that the error escapes is not enough — if
    # the broad `except Exception` swallowed it, the generation loop would run
    # and raise the same error one stage later from its own check_budget().
    # Zero generator calls is what proves it aborted at the PDF stage.
    assert capture["responder"].generate_calls == 0
    assert len(capture["fake"].embeddings.calls) == 1
