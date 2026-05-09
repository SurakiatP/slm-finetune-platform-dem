"""Unit tests for make_coverage_pool."""

from __future__ import annotations

import random
from collections import Counter

from ai_engine.data_gen.coverage_pool import make_coverage_pool


def test_pool_length_matches_request():
    pool = make_coverage_pool(["a", "b", "c"], 10, rng=random.Random(0))
    assert len(pool) == 10


def test_each_item_hit_at_least_floor_n_over_k_times():
    items = ["easy", "medium", "hard", "complex"]
    pool = make_coverage_pool(items, 17, rng=random.Random(0))
    counts = Counter(pool)
    for item in items:
        assert counts[item] >= 17 // len(items), (
            f"{item!r} only appeared {counts[item]} times in pool {pool}"
        )


def test_zero_length_returns_empty():
    assert make_coverage_pool(["x"], 0) == []


def test_empty_items_raises():
    import pytest

    with pytest.raises(ValueError):
        make_coverage_pool([], 5)


def test_negative_n_raises():
    import pytest

    with pytest.raises(ValueError):
        make_coverage_pool(["a"], -1)


def test_pool_is_shuffled():
    """With 100 items and a fixed RNG, the first few should not be a clean cycle."""
    items = ["a", "b", "c", "d", "e"]
    pool = make_coverage_pool(items, 100, rng=random.Random(42))
    # The unshuffled result would start with [a, b, c, d, e, a, b, c, ...].
    # After shuffle this is overwhelmingly unlikely.
    assert pool[:5] != items
