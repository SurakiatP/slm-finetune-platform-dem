"""Coverage-pool helper for diversity rotation.

When generating N samples per loop iteration, we want to walk through the
diversity-rule list (and the difficulty list) cyclically — every rule gets
hit at least floor(N/k) times, then a shuffled tail picks up the remainder.

Used to drive both `diversity_rules` and `DIFFICULTY_LEVELS` rotation in
the Generator prompt assembly stage.
"""

from __future__ import annotations

import random
from typing import Sequence, TypeVar

T = TypeVar("T")


def make_coverage_pool(
    items: Sequence[T],
    n: int,
    *,
    rng: random.Random | None = None,
) -> list[T]:
    """Build a length-`n` list cycling through `items` evenly.

    Each item appears `floor(n / len(items))` times, then the remainder
    is drawn (without replacement, then shuffled-back if it overruns) so
    the tail is randomised. The full result is shuffled before returning
    so the order doesn't reveal the cycle structure.

    Args:
        items: source pool. Must be non-empty.
        n: desired length of the returned pool.
        rng: optional random.Random instance for deterministic tests.
    """
    if not items:
        raise ValueError("items must be non-empty")
    if n < 0:
        raise ValueError("n must be >= 0")
    if n == 0:
        return []

    r = rng if rng is not None else random
    full_cycles = n // len(items)
    remainder = n - full_cycles * len(items)

    pool: list[T] = list(items) * full_cycles
    if remainder:
        # Pull `remainder` distinct items so the tail is balanced too.
        shuffled = list(items)
        r.shuffle(shuffled)
        pool.extend(shuffled[:remainder])
    r.shuffle(pool)
    return pool


__all__ = ["make_coverage_pool"]
