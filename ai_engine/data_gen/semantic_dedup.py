"""Near-duplicate detection via embedding cosine similarity.

Sits alongside `MinHashDeduplicator` (`minhash_dedup.py`) rather than
replacing it — MinHash catches lexical/paraphrase overlap cheaply; this
module catches *semantic* duplicates that read as different sentences but
mean the same thing, which no n-gram signature can see. Both can run in
the same SDG loop.

Why brute-force cosine instead of an ANN index (FAISS/HNSW/etc): each SDG
run's candidate pool tops out at a few thousand rows, and a brute-force
`matrix @ vector` over a few-thousand-row float32 matrix is sub-millisecond
on any CPU — well under the cost of the embedding call that produced the
vector in the first place. An ANN index earns its keep at millions of
rows with sub-linear query cost; at this scale it would only add a new
dependency, an index-build step, and a class of "index is stale" bugs for
no measurable speedup. Revisit if a run-scale requirement ever crosses
into the tens of thousands of rows.

Why 0.90: mirrors `MINHASH_THRESHOLD`'s calibration — conservative enough
that genuinely distinct-but-related rows (same topic, different question)
survive, while near-paraphrases that lexical MinHash might miss get
caught. See `constants.EMBEDDING_DEDUP_THRESHOLD`.

Why the degrade rule (embeddings outage must not fail an otherwise-good
SDG run): the embedding provider is one more remote dependency on top of
the OpenRouter generation/judge calls a run already depends on. A run
that has already spent budget generating and judging hundreds of good
rows must not be thrown away because the embedding endpoint had a bad
five minutes. So a failure in `embed()` degrades this deduplicator to a
permanent, silent pass-through (MinHash dedup, which runs independently,
still applies) rather than raising and aborting the run.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import numpy as np

from .constants import EMBEDDING_BATCH_SIZE, EMBEDDING_DEDUP_THRESHOLD
from .minhash_dedup import DedupOutcome

log = logging.getLogger(__name__)

# embed(texts) -> (vectors, prompt_tokens, model_id)
EmbedFn = Callable[[list[str]], Awaitable[tuple[list[list[float]], int, str]]]


class SemanticDeduplicator:
    """Stateful, in-memory cosine-similarity dedup tracker for one SDG run.

    Holds an L2-normalized float32 matrix of every vector seen so far
    (via `add_texts` seeding or a prior `filter` call). A candidate text
    is a duplicate when its cosine similarity to *any* stored vector is
    `>= threshold` — cosine similarity between L2-normalized vectors
    reduces to a dot product, so `filter`/`add_texts` normalize once on
    insert and duplicate-check via a single `matrix @ vector` matmul.

    Not thread-safe (single-consumer, single asyncio-task usage pattern
    matching the rest of the SDG loop) — unlike `UsageAccumulator`, no
    lock is taken here.
    """

    def __init__(
        self,
        embed: EmbedFn,
        *,
        threshold: float = EMBEDDING_DEDUP_THRESHOLD,
        batch_size: int = EMBEDDING_BATCH_SIZE,
        on_usage: Callable[[str, int], None] | None = None,
    ) -> None:
        self._embed = embed
        self._threshold = threshold
        self._batch_size = batch_size
        self._on_usage = on_usage
        self._matrix: np.ndarray | None = None  # (n, dim) float32, L2-normalized rows
        self._degraded = False
        self._warned = False

    @property
    def degraded(self) -> bool:
        """True once an embedding call has failed; sticky for the run."""
        return self._degraded

    def __len__(self) -> int:
        return 0 if self._matrix is None else self._matrix.shape[0]

    def _normalize(self, vectors: list[list[float]]) -> np.ndarray:
        arr = np.asarray(vectors, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        return arr / np.maximum(norms, 1e-12)

    def _append(self, arr: np.ndarray) -> None:
        if arr.shape[0] == 0:
            return
        if self._matrix is None:
            self._matrix = arr
        else:
            self._matrix = np.vstack([self._matrix, arr])

    async def _embed_batch(self, texts: list[str]) -> np.ndarray | None:
        """Embed one batch, degrading the deduplicator on any failure.

        Only the `await self._embed(...)` call itself is guarded — a
        subsequent exception raised by `on_usage` (e.g. the caller's
        budget check) is deliberately allowed to propagate out of this
        module uncaught; that is a budget-policy decision by the caller,
        not an embeddings-provider fault, and must not be swallowed as a
        "degrade".
        """
        try:
            vectors, prompt_tokens, model_id = await self._embed(texts)
        except Exception:
            self._degrade("embedding call raised")
            return None

        if len(vectors) != len(texts):
            self._degrade("embedding call returned mismatched vector count")
            return None
        dims = {len(v) for v in vectors}
        if len(dims) > 1:
            self._degrade("embedding call returned mismatched vector dimensionality")
            return None
        if self._matrix is not None and dims and dims != {self._matrix.shape[1]}:
            self._degrade("embedding call returned a dimensionality change mid-run")
            return None

        if self._on_usage is not None:
            self._on_usage(model_id, prompt_tokens)

        return self._normalize(vectors)

    def _degrade(self, reason: str) -> None:
        self._degraded = True
        if not self._warned:
            log.warning("SemanticDeduplicator degraded: %s; disabling for this run", reason)
            self._warned = True

    async def add_texts(self, texts: list[str]) -> None:
        """Embed and insert `texts` (seed preload). Empty strings are skipped."""
        if self._degraded:
            return
        non_empty = [t for t in texts if t]
        for start in range(0, len(non_empty), self._batch_size):
            batch = non_empty[start : start + self._batch_size]
            normalized = await self._embed_batch(batch)
            if normalized is None:
                return
            self._append(normalized)

    async def filter(self, rows: list[dict], *, key: str) -> DedupOutcome:
        """Drop rows whose `row[key]` embeds too close to anything seen so far.

        Rows are processed in input order; a surviving row's vector is
        appended immediately, so later rows in the *same* call dedup
        against it too (matching `MinHashDeduplicator.filter`'s
        within-batch semantics). Rows with empty text pass through as
        unique without being embedded.
        """
        if self._degraded:
            return DedupOutcome(unique=list(rows), duplicates_dropped=0)

        texts = [str(row.get(key, "")) for row in rows]
        non_empty_indices = [i for i, t in enumerate(texts) if t]

        # Embed all non-empty texts up front, batch by batch, keeping
        # vectors indexed by their row position so filtering below can
        # walk rows in original order.
        vectors_by_index: dict[int, np.ndarray] = {}
        for start in range(0, len(non_empty_indices), self._batch_size):
            idx_batch = non_empty_indices[start : start + self._batch_size]
            text_batch = [texts[i] for i in idx_batch]
            normalized = await self._embed_batch(text_batch)
            if normalized is None:
                # Degraded mid-filter: everything (including rows already
                # embedded in earlier batches of this same call) passes
                # through untouched, per the degrade contract.
                return DedupOutcome(unique=list(rows), duplicates_dropped=0)
            for i, vec in zip(idx_batch, normalized):
                vectors_by_index[i] = vec

        unique: list[dict] = []
        dropped = 0
        for i, row in enumerate(rows):
            vec = vectors_by_index.get(i)
            if vec is None:
                # Empty text: pass through as unique, not embedded.
                unique.append(row)
                continue
            if self._matrix is not None and self._matrix.shape[0] > 0:
                sims = self._matrix @ vec
                if float(sims.max()) >= self._threshold:
                    dropped += 1
                    continue
            self._append(vec.reshape(1, -1))
            unique.append(row)

        return DedupOutcome(unique=unique, duplicates_dropped=dropped)


__all__ = ["EmbedFn", "SemanticDeduplicator"]
