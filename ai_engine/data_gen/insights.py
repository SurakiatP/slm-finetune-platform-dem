"""Pure aggregation of `JudgeScore` results into a JSON-serialisable summary.

The SDG orchestrator scores every accepted/rejected row with the LLM judge
(`judge.py`) but doesn't keep the per-row scores around — they're too
granular to be useful after the run. What downstream consumers (the
dataset-detail API, the frontend's quality panel) actually want is a
compact rollup: overall mean per axis, a histogram of the distribution,
and the same breakdown re-sliced by an arbitrary grouping key (e.g. tool
name for `tool_calling`, or label for `classification`).

`JudgeScoreAggregator` is that rollup, computed incrementally as rows
stream through the SDG loop rather than buffering every `JudgeScore` and
aggregating at the end — the caller `.add()`s one score at a time (with
its grouping key) and asks for `.to_dict()` once, at the end of the run.

The output shape is pinned exactly (see `to_dict`'s docstring) because it
is written straight into a JSONB column and read back by other layers —
changing key names or the histogram bin count is a breaking change for
whoever reads that column, not just an internal refactor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .judge import JudgeScore

# Fixed-width histogram: 10 bins of width 0.1 spanning [0, 1].
JUDGE_HISTOGRAM_BINS = 10

_AXES = ("fidelity", "naturalness", "utility", "weighted")

# Grouping key used when the caller passes an empty string (or otherwise
# falsy) key — keeps `by_key` free of an empty-string dict key, which is
# legal JSON but an easy footgun for anything indexing by key downstream.
_UNKNOWN_KEY = "__unknown__"


def _bin_index(value: float) -> int:
    """Map a score in [0, 1] to a histogram bin in [0, 9].

    Clamped defensively even though `JudgeScore` fields are already
    `Field(..., ge=0.0, le=1.0)` — this function has no way to know that
    invariant holds for whatever it's called with, so it re-establishes
    it locally rather than trusting the caller.
    """
    clamped = min(max(value, 0.0), 1.0)
    return min(int(clamped * JUDGE_HISTOGRAM_BINS), JUDGE_HISTOGRAM_BINS - 1)


@dataclass
class _Bucket:
    """Running sums + histogram for one group (overall, or one `by_key` slice)."""

    count: int = 0
    sums: dict[str, float] = field(
        default_factory=lambda: {axis: 0.0 for axis in _AXES}
    )
    histograms: dict[str, list[int]] = field(
        default_factory=lambda: {
            axis: [0] * JUDGE_HISTOGRAM_BINS for axis in _AXES
        }
    )

    def add(self, score: JudgeScore) -> None:
        values = {
            "fidelity": score.fidelity,
            "naturalness": score.naturalness,
            "utility": score.utility,
            "weighted": score.weighted,
        }
        self.count += 1
        for axis, value in values.items():
            self.sums[axis] += value
            self.histograms[axis][_bin_index(value)] += 1

    def to_dict(self) -> dict[str, Any]:
        mean = {axis: round(self.sums[axis] / self.count, 4) for axis in _AXES}
        histogram = {axis: list(self.histograms[axis]) for axis in _AXES}
        return {"count": self.count, "mean": mean, "histogram": histogram}


class JudgeScoreAggregator:
    """Incrementally aggregates `JudgeScore`s into an overall + per-key rollup.

    Usage: call `.add(key, score)` once per judged row as the SDG loop
    produces them, then `.to_dict()` once at the end to get the JSONB-ready
    summary. `key` is whatever grouping the caller cares about (a tool
    name, a classification label, ...); an empty string is bucketed under
    `"__unknown__"` rather than kept as-is.
    """

    def __init__(self) -> None:
        self._overall = _Bucket()
        self._by_key: dict[str, _Bucket] = {}

    def add(self, key: str, score: JudgeScore) -> None:
        self._overall.add(score)
        bucket_key = key if key else _UNKNOWN_KEY
        self._by_key.setdefault(bucket_key, _Bucket()).add(score)

    def __len__(self) -> int:
        return self._overall.count

    def to_dict(self) -> dict[str, Any] | None:
        """Return the pinned rollup shape, or `None` if nothing was added.

        Shape (all fields always present when not `None`):

            {
              "count": int,
              "mean": {"fidelity": float, "naturalness": float,
                        "utility": float, "weighted": float},
              "histogram": {"fidelity": [10 ints], "naturalness": [...],
                             "utility": [...], "weighted": [...]},
              "by_key": {"<key>": {"count": int, "mean": {...},
                                     "histogram": {...}}, ...}
            }

        `by_key` entries have the same `count`/`mean`/`histogram` shape as
        the top level but no nested `by_key` of their own. `mean` values
        are rounded to 4 decimals; each `histogram` list is always exactly
        `JUDGE_HISTOGRAM_BINS` (10) long. Every value here is a plain
        int/float/str/dict/list so the result survives `json.dumps`
        unchanged, as required to land in a JSONB column.
        """
        if self._overall.count == 0:
            return None
        result = self._overall.to_dict()
        result["by_key"] = {
            key: bucket.to_dict() for key, bucket in self._by_key.items()
        }
        return result


__all__ = ["JUDGE_HISTOGRAM_BINS", "JudgeScoreAggregator"]
