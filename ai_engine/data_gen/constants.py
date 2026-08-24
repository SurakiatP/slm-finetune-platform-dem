"""Tunable thresholds, batch sizes, and stop conditions for the SDG loop.

Ported verbatim from parks's research-grade SDG scripts (Phase 9 spec §11).
Group all knobs in one place so adjusting them doesn't require hunting
through the orchestrator.
"""

from __future__ import annotations

# ---- Loop control ----------------------------------------------------------

MAX_LOOPS = 20
"""Hard cap on SDG loop iterations. Past this we stop and persist whatever
we have collected (partial success)."""

MAX_CONSECUTIVE_FAILURES = 5
"""A 'failed loop' adds zero accepted rows. After this many in a row, raise
SDGAbortedError instead of looping forever."""

# ---- Quality gates ---------------------------------------------------------

JUDGE_THRESHOLD = 0.7
"""Weighted judge score (0.4·fidelity + 0.3·naturalness + 0.3·utility) below
this value rejects the row."""

MINHASH_THRESHOLD = 0.90
"""Jaccard similarity at which two rows count as near-duplicates."""

MINHASH_NUM_PERM = 128
"""MinHash permutation count. Higher = more accurate, more memory/CPU."""

MINHASH_NGRAM_SIZE = 5
"""Character-n-gram width used to build the MinHash signature."""

# ---- Output volumes --------------------------------------------------------

CANDIDATES_PER_GEN_CALL = 5
"""Each Generator prompt asks for 5 candidates. Locked into the prompt
template; do not change without updating the prompt."""

GENERATOR_BATCH_SIZE = 100
"""Concurrency for AsyncOpenRouterClient.chat_batch on Generator calls."""

JUDGE_BATCH_SIZE = 100
"""Concurrency for AsyncOpenRouterClient.chat_batch on Judge calls."""

JUDGE_ROWS_PER_CALL = 10
"""Rows bundled into a single batched Judge prompt/response when using
parse_judge_batch_response. Locked into the batch prompt template; do not
change without updating the prompt."""

# ---- Adaptive over-generation ---------------------------------------------

INITIAL_OVER_GEN_MULT = 1.5
MIN_OVER_GEN_MULT = 1.5
MAX_OVER_GEN_MULT = 8.0

# ---- Sentinel quotas (classification + tool_calling) -----------------------

SENTINEL_RATIO = 0.10
"""Fraction of generated rows reserved for the sentinel class
('unknown' / 'no_tool_needed')."""

CLASSIFICATION_SENTINEL_LABEL = "unknown"
TOOL_CALLING_SENTINEL_NAME = "no_tool_needed"
TOOL_CALLING_SENTINEL_DESCRIPTION = (
    "Use this when the user's request does not match any available tool "
    "(off-topic small talk, ambiguous queries, or requests outside the "
    "catalog). Returns no parameters; the orchestrator handles the response."
)

# ---- Difficulty rotation ---------------------------------------------------

DIFFICULTY_LEVELS: tuple[str, ...] = ("easy", "medium", "hard", "complex-structure")
"""Difficulty pool cycled per Generator call to widen sample distribution."""

# ---- Upload guards ---------------------------------------------------------

MAX_SEED_PDF_BYTES = 25 * 1024 * 1024
"""25 MiB cap on PDF uploads (QA only). Larger PDFs almost certainly aren't
seed material."""

MAX_SEED_PDF_PAGES = 100
"""Page cap on PDF uploads. Multimodal Gemini calls scale with page count."""

# ---- Per-call budgets ------------------------------------------------------

PER_CALL_TIMEOUT_SECONDS = 120.0
"""Wall-clock cap on a single LLM call. Tenacity retries inside the wrapper
each attempt up to its own stop condition."""


__all__ = [
    "MAX_LOOPS",
    "MAX_CONSECUTIVE_FAILURES",
    "JUDGE_THRESHOLD",
    "MINHASH_THRESHOLD",
    "MINHASH_NUM_PERM",
    "MINHASH_NGRAM_SIZE",
    "CANDIDATES_PER_GEN_CALL",
    "GENERATOR_BATCH_SIZE",
    "JUDGE_BATCH_SIZE",
    "JUDGE_ROWS_PER_CALL",
    "INITIAL_OVER_GEN_MULT",
    "MIN_OVER_GEN_MULT",
    "MAX_OVER_GEN_MULT",
    "SENTINEL_RATIO",
    "CLASSIFICATION_SENTINEL_LABEL",
    "TOOL_CALLING_SENTINEL_NAME",
    "TOOL_CALLING_SENTINEL_DESCRIPTION",
    "DIFFICULTY_LEVELS",
    "MAX_SEED_PDF_BYTES",
    "MAX_SEED_PDF_PAGES",
    "PER_CALL_TIMEOUT_SECONDS",
]
