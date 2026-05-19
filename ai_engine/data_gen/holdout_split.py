"""Stratified holdout split for SDG-generated rows.

The SDG worker over-generates by `holdout_size` rows; this module slices the
result into (train, holdout) such that the holdout is a faithful sample of
the overall distribution:

  - classification - stratified by `label`
  - tool_calling   - stratified by tool `name` (parsed from JSON `answer`)
  - qa             - random (no structural strata)

Pure Python - no DB, no MinIO, no network. Designed to be unit-testable.
"""

from __future__ import annotations

import json
import logging
import random
from collections import defaultdict
from typing import Any

from api.schemas.enums import TaskType

log = logging.getLogger(__name__)

_TOOL_NAME_FALLBACK = "__unparseable__"


def split_rows(
    rows: list[dict[str, Any]],
    task_type: TaskType,
    holdout_size: int,
    rng: random.Random,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return `(train, holdout)`. Total = len(rows).

    Args:
        rows: SDG-validated rows (already deduped + judge-passed).
        task_type: drives stratification strategy.
        holdout_size: target holdout count. 0 = no holdout. If `holdout_size >=
            len(rows)`, all rows go to holdout and `train` is empty.
        rng: caller-supplied RNG (seeded for determinism in tests).
    """
    if holdout_size <= 0 or not rows:
        return list(rows), []
    if holdout_size >= len(rows):
        return [], list(rows)

    key_fn = _key_fn_for_task(task_type)
    if key_fn is None:
        shuffled = list(rows)
        rng.shuffle(shuffled)
        return shuffled[holdout_size:], shuffled[:holdout_size]

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[key_fn(row)].append(row)
    for bucket in buckets.values():
        rng.shuffle(bucket)

    total = len(rows)
    per_bucket: dict[str, int] = {}
    for key, bucket in buckets.items():
        share = round(holdout_size * len(bucket) / total)
        per_bucket[key] = min(share, len(bucket))

    allocated = sum(per_bucket.values())
    diff = holdout_size - allocated
    if diff != 0:
        sorted_keys = sorted(buckets.keys(), key=lambda k: -len(buckets[k]))
        idx = 0
        while diff != 0 and sorted_keys:
            key = sorted_keys[idx % len(sorted_keys)]
            if diff > 0 and per_bucket[key] < len(buckets[key]):
                per_bucket[key] += 1
                diff -= 1
            elif diff < 0 and per_bucket[key] > 0:
                per_bucket[key] -= 1
                diff += 1
            idx += 1
            if idx > len(sorted_keys) * (abs(diff) + 2):
                log.warning(
                    "split_rows: could not balance holdout exactly; diff=%d", diff
                )
                break

    holdout: list[dict[str, Any]] = []
    train: list[dict[str, Any]] = []
    for key, bucket in buckets.items():
        n_holdout = per_bucket[key]
        holdout.extend(bucket[:n_holdout])
        train.extend(bucket[n_holdout:])

    rng.shuffle(holdout)
    rng.shuffle(train)
    return train, holdout


def _key_fn_for_task(task_type: TaskType):
    if task_type is TaskType.CLASSIFICATION:
        return _classification_key
    if task_type is TaskType.TOOL_CALLING:
        return _tool_calling_key
    return None


def _classification_key(row: dict[str, Any]) -> str:
    return str(row.get("label", "__missing__"))


def _tool_calling_key(row: dict[str, Any]) -> str:
    raw_answer = row.get("answer")
    if not isinstance(raw_answer, str):
        return _TOOL_NAME_FALLBACK
    try:
        parsed = json.loads(raw_answer)
    except (json.JSONDecodeError, ValueError):
        return _TOOL_NAME_FALLBACK
    if not isinstance(parsed, dict):
        return _TOOL_NAME_FALLBACK
    name = parsed.get("name")
    if not isinstance(name, str) or not name:
        return _TOOL_NAME_FALLBACK
    return name


__all__ = ["split_rows"]
