"""Exact-duplicate removal for synthetic data.

PoC scope: hash the canonical user-facing text per task and reject re-hits.
Near-duplicate detection (MinHash, embedding cosine) is a Phase 8 polish item.

The same `Deduplicator` instance is used across batches in one generation run,
seeded with the seed examples up front, so generated rows that collide with
seeds are also rejected.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from api.schemas.enums import TaskType


# Field that uniquely identifies a row (for dedup purposes) by task type.
_KEY_FIELD: dict[TaskType, str] = {
    TaskType.CLASSIFICATION: "text",
    TaskType.TOOL_CALLING: "question",
    TaskType.QA: "question",
}


def dedup_key(task_type: TaskType, row: dict[str, Any]) -> str:
    """Compute the dedup hash for one row. Robust to whitespace + casing."""
    field = _KEY_FIELD[task_type]
    raw = row.get(field, "")
    if not isinstance(raw, str):
        raw = str(raw)
    # Normalize: collapse whitespace, lowercase, strip.
    normalized = " ".join(raw.split()).strip().lower()
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DedupResult:
    unique: list[dict[str, Any]]
    duplicate_count: int


class Deduplicator:
    """Stateful dedup tracker for one SDG run."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def __len__(self) -> int:
        return len(self._seen)

    def seed(self, task_type: TaskType, rows: list[dict[str, Any]]) -> None:
        """Pre-load the seen set (e.g. with the user's seed examples).

        Generated rows that match these are treated as duplicates.
        """
        for row in rows:
            self._seen.add(dedup_key(task_type, row))

    def filter(self, task_type: TaskType, rows: list[dict[str, Any]]) -> DedupResult:
        """Drop rows whose key is already in `seen`. Adds new keys to `seen`."""
        unique: list[dict[str, Any]] = []
        duplicate_count = 0
        for row in rows:
            key = dedup_key(task_type, row)
            if key in self._seen:
                duplicate_count += 1
                continue
            self._seen.add(key)
            unique.append(row)
        return DedupResult(unique=unique, duplicate_count=duplicate_count)


__all__ = ["dedup_key", "DedupResult", "Deduplicator"]
