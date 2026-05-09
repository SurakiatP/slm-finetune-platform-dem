"""Near-duplicate detection via MinHash LSH (datasketch).

Replaces the exact-string `Deduplicator` for the SDG generation loop. The
existing `Deduplicator` stays in place for upload-time seed dedup (where
exact-match is the right semantic).

Properties:
  • Char-level n-grams (default 5-gram) so paraphrases collide.
  • Threshold 0.90 (Jaccard) — tuned conservatively to avoid culling
    genuinely diverse rows.
  • Short-text guard: if normalised text is shorter than the n-gram width,
    feed the whole text as a single token. Without this, MinHash on
    10-character classification labels degenerates to "every short text
    looks the same."
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from datasketch import MinHash, MinHashLSH

from .constants import MINHASH_NGRAM_SIZE, MINHASH_NUM_PERM, MINHASH_THRESHOLD

_WHITESPACE_RE = re.compile(r"\s+")


def _normalise(text: str) -> str:
    """Lowercase + collapse whitespace + strip. Robust to formatting drift."""
    return _WHITESPACE_RE.sub(" ", text).strip().lower()


def compute_minhash(
    text: str,
    *,
    num_perm: int = MINHASH_NUM_PERM,
    ngram_size: int = MINHASH_NGRAM_SIZE,
) -> MinHash:
    """Build a MinHash signature for one text.

    Encoding choice: char n-grams over the normalised text. Below the
    n-gram width we fall back to whole-text encoding so signatures of
    short labels don't all collide.
    """
    m = MinHash(num_perm=num_perm)
    clean = _normalise(text)
    if len(clean) < ngram_size:
        # Short-text guard: hash the whole string. Below this length, the
        # n-gram set would be empty and every short text would look
        # identical to the LSH (false positives).
        m.update(clean.encode("utf-8"))
        return m
    for i in range(len(clean) - ngram_size + 1):
        m.update(clean[i : i + ngram_size].encode("utf-8"))
    return m


@dataclass(frozen=True)
class DedupOutcome:
    """One dedup pass's result."""

    unique: list[dict]
    duplicates_dropped: int


class MinHashDeduplicator:
    """Stateful LSH-backed dedup tracker for one SDG run.

    Insert `seed` rows up front; subsequent `filter` calls reject rows
    whose MinHash signature is similar (Jaccard >= threshold) to any
    previously inserted signature.
    """

    def __init__(
        self,
        *,
        threshold: float = MINHASH_THRESHOLD,
        num_perm: int = MINHASH_NUM_PERM,
        ngram_size: int = MINHASH_NGRAM_SIZE,
    ) -> None:
        if not 0.0 < threshold <= 1.0:
            raise ValueError("threshold must be in (0, 1]")
        self._lsh = MinHashLSH(threshold=threshold, num_perm=num_perm)
        self._num_perm = num_perm
        self._ngram_size = ngram_size
        self._counter = 0

    def __len__(self) -> int:
        return self._counter

    def _next_key(self) -> str:
        self._counter += 1
        return f"row_{self._counter}"

    def add(self, text: str) -> None:
        """Insert a single text into the LSH (for seeding)."""
        m = compute_minhash(text, num_perm=self._num_perm, ngram_size=self._ngram_size)
        self._lsh.insert(self._next_key(), m)

    def is_duplicate(self, text: str) -> bool:
        """Check whether `text` collides with anything already inserted."""
        m = compute_minhash(text, num_perm=self._num_perm, ngram_size=self._ngram_size)
        return bool(self._lsh.query(m))

    def filter(self, rows: list[dict], *, key: str) -> DedupOutcome:
        """Drop rows whose `row[key]` collides with anything seen so far.

        Inserts each surviving row's signature so subsequent rows in the
        same batch are also de-duped against it.
        """
        unique: list[dict] = []
        dropped = 0
        for row in rows:
            text = str(row.get(key, ""))
            m = compute_minhash(
                text, num_perm=self._num_perm, ngram_size=self._ngram_size
            )
            if self._lsh.query(m):
                dropped += 1
                continue
            self._lsh.insert(self._next_key(), m)
            unique.append(row)
        return DedupOutcome(unique=unique, duplicates_dropped=dropped)


__all__ = [
    "compute_minhash",
    "DedupOutcome",
    "MinHashDeduplicator",
]
