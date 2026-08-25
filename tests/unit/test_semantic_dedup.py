"""Unit tests for `ai_engine.data_gen.semantic_dedup.SemanticDeduplicator`.

No network: `embed` is always a stub closure returning fixed, precomputed
vectors so cosine similarity is exactly controlled per test.
"""

from __future__ import annotations

import math

import pytest

from ai_engine.data_gen.semantic_dedup import SemanticDeduplicator

MODEL_ID = "test-embedding-model"


def make_embed(vector_map: dict[str, list[float]], calls: list[list[str]] | None = None):
    """Return an EmbedFn stub that looks vectors up by text, recording calls."""

    async def embed(texts: list[str]) -> tuple[list[list[float]], int, str]:
        if calls is not None:
            calls.append(list(texts))
        vectors = [vector_map[t] for t in texts]
        return vectors, len(texts) * 10, MODEL_ID

    return embed


def boundary_vectors(threshold: float) -> tuple[list[float], list[float], list[float]]:
    """Return (base, at_threshold, just_below) 2D unit-ish vectors such that
    cosine(base, at_threshold) == threshold exactly and
    cosine(base, just_below) is a hair under threshold.
    """
    base = [1.0, 0.0]
    theta = math.acos(threshold)
    at_threshold = [math.cos(theta), math.sin(theta)]
    theta_below = theta + 0.05  # larger angle => smaller cosine
    just_below = [math.cos(theta_below), math.sin(theta_below)]
    return base, at_threshold, just_below


@pytest.mark.asyncio
async def test_identical_vectors_dropped():
    vmap = {"a": [1.0, 0.0], "b": [1.0, 0.0]}
    dedup = SemanticDeduplicator(make_embed(vmap), threshold=0.90)
    out = await dedup.filter([{"text": "a"}, {"text": "b"}], key="text")
    assert len(out.unique) == 1
    assert out.duplicates_dropped == 1


@pytest.mark.asyncio
async def test_orthogonal_vectors_survive():
    vmap = {"a": [1.0, 0.0], "b": [0.0, 1.0]}
    dedup = SemanticDeduplicator(make_embed(vmap), threshold=0.90)
    out = await dedup.filter([{"text": "a"}, {"text": "b"}], key="text")
    assert len(out.unique) == 2
    assert out.duplicates_dropped == 0


@pytest.mark.asyncio
async def test_threshold_boundary_exact_drops_just_below_survives():
    threshold = 0.90
    base, at_threshold, just_below = boundary_vectors(threshold)

    # sim exactly == threshold => drop
    vmap = {"base": base, "at": at_threshold}
    dedup = SemanticDeduplicator(make_embed(vmap), threshold=threshold)
    out = await dedup.filter([{"text": "base"}, {"text": "at"}], key="text")
    assert out.duplicates_dropped == 1
    assert len(out.unique) == 1

    # sim just below threshold => survive
    vmap2 = {"base": base, "below": just_below}
    dedup2 = SemanticDeduplicator(make_embed(vmap2), threshold=threshold)
    out2 = await dedup2.filter([{"text": "base"}, {"text": "below"}], key="text")
    assert out2.duplicates_dropped == 0
    assert len(out2.unique) == 2


@pytest.mark.asyncio
async def test_batching_calls_embed_once_per_batch_and_covers_every_text():
    texts = [f"row{i}" for i in range(5)]
    # All orthogonal-ish distinct vectors (one-hot in 5D) so nothing dedups.
    vmap = {t: [1.0 if j == i else 0.0 for j in range(5)] for i, t in enumerate(texts)}
    calls: list[list[str]] = []
    dedup = SemanticDeduplicator(make_embed(vmap, calls), threshold=0.90, batch_size=2)

    rows = [{"text": t} for t in texts]
    out = await dedup.filter(rows, key="text")

    assert len(calls) == 3  # batches of 2, 2, 1
    all_called_texts = [t for batch in calls for t in batch]
    assert sorted(all_called_texts) == sorted(texts)
    assert len(out.unique) == 5
    assert out.duplicates_dropped == 0


@pytest.mark.asyncio
async def test_within_batch_dedup_two_identical_texts_one_call():
    vmap = {"dup": [1.0, 0.0]}

    async def embed(texts: list[str]) -> tuple[list[list[float]], int, str]:
        return [vmap["dup"] for _ in texts], len(texts) * 10, MODEL_ID

    dedup = SemanticDeduplicator(embed, threshold=0.90)
    out = await dedup.filter([{"text": "dup"}, {"text": "dup"}], key="text")
    assert len(out.unique) == 1
    assert out.duplicates_dropped == 1


@pytest.mark.asyncio
async def test_add_texts_seeds_and_later_filter_drops_twin():
    vmap = {"seed": [1.0, 0.0], "twin": [1.0, 0.0]}
    dedup = SemanticDeduplicator(make_embed(vmap), threshold=0.90)

    await dedup.add_texts(["seed"])
    assert len(dedup) == 1

    out = await dedup.filter([{"text": "twin"}], key="text")
    assert out.duplicates_dropped == 1
    assert len(out.unique) == 0


@pytest.mark.asyncio
async def test_empty_text_rows_pass_through_without_embedding():
    calls: list[list[str]] = []
    vmap = {"a": [1.0, 0.0]}
    dedup = SemanticDeduplicator(make_embed(vmap, calls), threshold=0.90)

    out = await dedup.filter([{"text": ""}, {"text": "a"}, {}], key="text")
    assert len(out.unique) == 3
    assert out.duplicates_dropped == 0
    # Only the non-empty text should have been embedded.
    assert calls == [["a"]]


@pytest.mark.asyncio
async def test_degrade_on_error_all_rows_pass_and_no_further_embed_attempts():
    calls: list[list[str]] = []

    async def failing_embed(texts: list[str]) -> tuple[list[list[float]], int, str]:
        calls.append(list(texts))
        raise RuntimeError("embedding service unavailable")

    dedup = SemanticDeduplicator(failing_embed, threshold=0.90)
    assert dedup.degraded is False

    rows = [{"text": "a"}, {"text": "b"}]
    out = await dedup.filter(rows, key="text")

    assert out.unique == rows
    assert out.duplicates_dropped == 0
    assert dedup.degraded is True
    assert len(calls) == 1

    # Subsequent filter/add_texts calls must not attempt to embed again.
    out2 = await dedup.filter(rows, key="text")
    assert out2.unique == rows
    assert out2.duplicates_dropped == 0
    await dedup.add_texts(["c"])
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_on_usage_receives_model_and_tokens_per_batch():
    texts = [f"row{i}" for i in range(4)]
    vmap = {t: [1.0 if j == i else 0.0 for j in range(4)] for i, t in enumerate(texts)}
    usage_calls: list[tuple[str, int]] = []

    def on_usage(model_id: str, prompt_tokens: int) -> None:
        usage_calls.append((model_id, prompt_tokens))

    dedup = SemanticDeduplicator(
        make_embed(vmap), threshold=0.90, batch_size=2, on_usage=on_usage
    )
    rows = [{"text": t} for t in texts]
    await dedup.filter(rows, key="text")

    assert len(usage_calls) == 2  # 2 batches of 2
    for model_id, prompt_tokens in usage_calls:
        assert model_id == MODEL_ID
        assert prompt_tokens == 20  # 2 texts * 10 tokens


@pytest.mark.asyncio
async def test_on_usage_exception_propagates():
    vmap = {"a": [1.0, 0.0]}

    def on_usage(model_id: str, prompt_tokens: int) -> None:
        raise ValueError("budget exceeded")

    dedup = SemanticDeduplicator(make_embed(vmap), threshold=0.90, on_usage=on_usage)

    with pytest.raises(ValueError, match="budget exceeded"):
        await dedup.filter([{"text": "a"}], key="text")

    # Not a degrade — on_usage failures are a caller-policy decision.
    assert dedup.degraded is False
