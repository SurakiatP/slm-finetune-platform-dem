"""Unit tests for MinHashDeduplicator + compute_minhash short-text guard."""

from __future__ import annotations

from ai_engine.data_gen.minhash_dedup import (
    MinHashDeduplicator,
    compute_minhash,
)


def test_exact_match_is_duplicate():
    d = MinHashDeduplicator()
    d.add("hello world")
    assert d.is_duplicate("hello world") is True


def test_near_paraphrase_is_duplicate_at_default_threshold():
    """A trivial whitespace + casing variant should still collide."""
    d = MinHashDeduplicator()
    d.add("How do I reset my password?")
    assert d.is_duplicate("how do i reset my password") is True


def test_distinct_texts_not_duplicate():
    d = MinHashDeduplicator()
    d.add("How do I reset my password?")
    assert d.is_duplicate("What is the capital of France?") is False


def test_short_text_guard_avoids_universal_collision():
    """Below ngram width (5), every short label would otherwise collapse to
    the same empty-shingle signature. Guard ensures distinct labels stay
    distinct.
    """
    d = MinHashDeduplicator()
    d.add("yes")
    assert d.is_duplicate("yes") is True
    # "no" is shorter than the ngram_size and distinct content.
    assert d.is_duplicate("no") is False


def test_filter_dedups_within_a_single_batch():
    """When the same row appears twice in a batch, only the first survives."""
    d = MinHashDeduplicator()
    rows = [
        {"text": "alpha"},
        {"text": "alpha"},        # exact dup of row 0
        {"text": "beta"},
        {"text": "alpha "},       # whitespace variant; should still collide
        {"text": "gamma"},
    ]
    out = d.filter(rows, key="text")
    surviving_texts = [r["text"] for r in out.unique]
    assert "alpha" in surviving_texts
    assert "beta" in surviving_texts
    assert "gamma" in surviving_texts
    assert out.duplicates_dropped == 2


def test_compute_minhash_short_string_uses_whole_string():
    """Sanity: two different short strings produce different signatures."""
    a = compute_minhash("hi")
    b = compute_minhash("ok")
    assert a.jaccard(b) < 0.5


def test_thai_and_mixed_text_supported():
    """The dedup must work on non-ASCII content."""
    d = MinHashDeduplicator()
    d.add("กรุงเทพคือเมืองหลวงของประเทศไทย")
    assert d.is_duplicate("กรุงเทพคือเมืองหลวงของประเทศไทย") is True
    assert d.is_duplicate("Bangkok is the capital of Thailand") is False
