"""Token-usage accumulation and budget arithmetic for the SDG pipeline.

Pure domain logic (hexagonal rule, see workspace `CLAUDE.md`): this module
must never import `fastapi`, `celery`, `sqlalchemy`, or `api.core.config`,
and must never touch a database. It knows nothing about Postgres price
tables or Celery task state — a worker resolves prices from wherever
pricing config lives and hands this module plain
`{model: (usd_per_prompt_token, usd_per_completion_token)}` floats. That is
what keeps this file free of `api.core.config`.

The object built here (`UsageAccumulator`) is constructed by a Celery
worker and passed *into* `SyntheticDataGenerator.generate()`, which calls
`.add(...)` once per OpenRouter response as the SDG loop runs.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass

# Canonical OpenRouter-spend stage strings. Every caller (generator, judge,
# meta-prompter, format detector, PDF/QA path, evaluation's LLM judge) must
# import these rather than re-spelling the literal, so a typo in one layer
# can't silently create a second, unmerged usage bucket for what should be
# the same stage.
STAGE_META_PROMPT = "meta_prompt"
STAGE_GENERATE = "generate"
STAGE_JUDGE = "judge"
STAGE_PDF_QA = "pdf_qa"
STAGE_FORMAT_DETECTION = "format_detection"
# The evaluation run's LLM judge — deliberately NOT `STAGE_JUDGE`, which is
# the SDG generator's own quality judge. They are different pipelines with
# different cost profiles (SDG judges each generated row once during
# generation; evaluation judges each eval row against a trained model), and
# collapsing them into one bucket would make `GET /usage` unable to answer
# "what did evaluation cost me" at all.
STAGE_EVAL_JUDGE = "eval_judge"


@dataclass(frozen=True)
class UsageEntry:
    """One aggregated (model, stage) usage row — never one row per API call."""

    model: str
    stage: str
    prompt_tokens: int
    completion_tokens: int


class SDGBudgetExceededError(RuntimeError):
    """Raised by `UsageAccumulator.check_budget()` once spend reaches the limit.

    The `SDG` in the name is historical — this accumulator now also caps the
    evaluation run's LLM judge (`STAGE_EVAL_JUDGE`). The class name is kept
    for import stability; the message deliberately is not, because an
    operator reading "SDG budget exceeded" on a *cancelled evaluation* would
    go looking in the wrong pipeline.
    """

    def __init__(self, spent_usd: float, budget_remaining_usd: float) -> None:
        self.spent_usd = spent_usd
        self.budget_remaining_usd = budget_remaining_usd
        super().__init__(
            f"OpenRouter budget exceeded: spent ${spent_usd:.4f} of "
            f"${budget_remaining_usd:.4f} remaining budget"
        )


class UsageAccumulator:
    """Aggregates token usage per (model, stage) and enforces a $ budget.

    Why aggregate rather than log per-call: a 1000-sample run with judging
    makes 2000+ API calls (generator + multiple judge candidates per
    candidate set, meta-prompting, format detection, PDF/QA), but the
    caller only needs to persist/inspect a handful of rows — one per
    distinct (model, stage) pair, typically 3-5 for a whole run. `add()`
    is therefore called on the hot path once per API response and mutates
    an in-place aggregate instead of appending to a growing list.

    Thread-safety: `add()` is invoked both from the asyncio event loop
    (Generator/Judge batch calls) and from worker threads via
    `asyncio.to_thread(...)` (the sync PDF/QA + format-detection calls),
    so all mutation is guarded by a `threading.Lock`.
    """

    def __init__(
        self,
        *,
        prices: Mapping[str, tuple[float, float]] | None = None,
        budget_remaining_usd: float | None = None,
    ) -> None:
        # prices[model] = (usd_per_prompt_token, usd_per_completion_token).
        # Plain floats resolved by the caller (worker) — this class never
        # looks a price up anywhere itself.
        self._prices: Mapping[str, tuple[float, float]] = prices or {}
        self._budget_remaining_usd = budget_remaining_usd
        self._lock = threading.Lock()
        # Keyed on (model, stage) so repeated calls for the same pair merge
        # into a single running total instead of growing unboundedly.
        self._totals: dict[tuple[str, str], list[int]] = {}

    def add(
        self,
        model: str,
        stage: str,
        prompt_tokens: int | None,
        completion_tokens: int | None,
    ) -> None:
        """Record one API call's usage, merged into its (model, stage) bucket.

        `prompt_tokens`/`completion_tokens` coerce `None` to 0 — OpenRouter
        occasionally omits usage on error/empty responses (see
        `ChatResult` in `openrouter_client.py`), and a dropped call must
        not crash accounting for an otherwise-successful run.
        """
        pt = prompt_tokens or 0
        ct = completion_tokens or 0
        key = (model, stage)
        with self._lock:
            if key not in self._totals:
                self._totals[key] = [0, 0]
            bucket = self._totals[key]
            bucket[0] += pt
            bucket[1] += ct

    def entries(self) -> list[UsageEntry]:
        """Return the current aggregate, one `UsageEntry` per (model, stage)."""
        with self._lock:
            return [
                UsageEntry(
                    model=model,
                    stage=stage,
                    prompt_tokens=totals[0],
                    completion_tokens=totals[1],
                )
                for (model, stage), totals in self._totals.items()
            ]

    @property
    def spent_usd(self) -> float:
        """Total USD spend across priced entries only.

        Known limitation: a model absent from `prices` contributes 0 to
        this sum even though real usage occurred — see
        `has_unpriced_usage`, which is the signal a caller should log
        about so silent under-counting doesn't go unnoticed.
        """
        total = 0.0
        for entry in self.entries():
            price = self._prices.get(entry.model)
            if price is None:
                continue
            prompt_price, completion_price = price
            total += entry.prompt_tokens * prompt_price
            total += entry.completion_tokens * completion_price
        return total

    @property
    def has_unpriced_usage(self) -> bool:
        """True if any model with accumulated usage has no entry in `prices`."""
        return any(entry.model not in self._prices for entry in self.entries())

    def check_budget(self) -> None:
        """Raise `SDGBudgetExceededError` once spend reaches the budget.

        Why this exists mid-run, not just at submit time: submit-only
        budget enforcement was explicitly considered and rejected — a run
        that starts at $0 and burns $50 over a three-hour SDG loop would
        never be stopped by a check that only runs once, before the first
        API call. Calling `check_budget()` after each `add()` (or at
        natural loop-iteration boundaries) is what actually caps spend.

        No-op when `budget_remaining_usd is None` (unlimited budget).
        Otherwise raises once `spent_usd >= budget_remaining_usd`
        (strictly greater is intentionally not required — hitting the
        limit exactly still stops the run).

        Known limitation: unpriced models (see `has_unpriced_usage`)
        contribute 0 to `spent_usd`, so this check cannot catch overspend
        on a model missing from `prices` — callers should log
        `has_unpriced_usage` separately rather than relying on this to
        catch that case.
        """
        if self._budget_remaining_usd is None:
            return
        spent = self.spent_usd
        if spent >= self._budget_remaining_usd:
            raise SDGBudgetExceededError(spent, self._budget_remaining_usd)


__all__ = [
    "STAGE_META_PROMPT",
    "STAGE_GENERATE",
    "STAGE_JUDGE",
    "STAGE_PDF_QA",
    "STAGE_FORMAT_DETECTION",
    "STAGE_EVAL_JUDGE",
    "UsageEntry",
    "SDGBudgetExceededError",
    "UsageAccumulator",
]
