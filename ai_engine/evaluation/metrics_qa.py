"""QA metrics — ROUGE-1/2/L, BLEU, exact match.

Pure domain code. The third-party metric libraries (`rouge-score`, `evaluate`,
`sacrebleu`) live in `[eval]` extras and are imported lazily.

We compute:
  • Exact match (case + whitespace normalised)
  • ROUGE-1 / ROUGE-2 / ROUGE-L F-measures (mean across rows)
  • Corpus BLEU via sacrebleu (single number)
"""

from __future__ import annotations

import re
from typing import Any

_WHITESPACE_RE = re.compile(r"\s+")


def compute_metrics(
    *,
    predicted: list[str],
    expected: list[str],
) -> dict[str, Any]:
    """Compute QA metrics over parallel `predicted` / `expected` lists.

    Returns:
        ``{"exact_match", "rouge1", "rouge2", "rougeL", "bleu", "n"}``.
    """
    if len(predicted) != len(expected):
        raise ValueError(
            f"predicted/expected length mismatch: {len(predicted)} vs {len(expected)}"
        )
    if not predicted:
        raise ValueError("compute_metrics: empty inputs")

    em = _exact_match(predicted, expected)
    rouge_scores = _rouge_means(predicted, expected)
    bleu = _bleu(predicted, expected)

    return {
        "exact_match": em,
        "rouge1": rouge_scores["rouge1"],
        "rouge2": rouge_scores["rouge2"],
        "rougeL": rouge_scores["rougeL"],
        "bleu": bleu,
        "n": len(predicted),
    }


# ---- helpers ---------------------------------------------------------------


def _normalize(s: str) -> str:
    return _WHITESPACE_RE.sub(" ", (s or "").strip().lower())


def _exact_match(predicted: list[str], expected: list[str]) -> float:
    n = len(predicted)
    if n == 0:
        return 0.0
    hits = sum(1 for p, e in zip(predicted, expected) if _normalize(p) == _normalize(e))
    return hits / n


def _rouge_means(predicted: list[str], expected: list[str]) -> dict[str, float]:
    """Mean F-measure for ROUGE-1/2/L. Returns 0.0 for any row that errors."""
    from rouge_score.rouge_scorer import RougeScorer  # type: ignore[import-not-found]

    scorer = RougeScorer(["rouge1", "rouge2", "rougeL"], use_stemmer=True)
    sums = {"rouge1": 0.0, "rouge2": 0.0, "rougeL": 0.0}
    n = len(predicted)
    for pred, exp in zip(predicted, expected):
        if not isinstance(pred, str) or not isinstance(exp, str):
            continue
        scores = scorer.score(exp, pred)
        for k in sums:
            sums[k] += float(scores[k].fmeasure)
    return {k: (v / n if n else 0.0) for k, v in sums.items()}


def _bleu(predicted: list[str], expected: list[str]) -> float:
    """Corpus BLEU via sacrebleu. References must be a list-of-lists."""
    from sacrebleu.metrics import BLEU  # type: ignore[import-not-found]

    bleu = BLEU()
    # sacrebleu expects [[ref1_for_sys1, ref1_for_sys2, ...]] — single reference per hyp.
    score = bleu.corpus_score(predicted, [expected])
    return float(score.score) / 100.0  # sacrebleu reports 0–100; normalise to 0–1.


__all__ = ["compute_metrics"]
