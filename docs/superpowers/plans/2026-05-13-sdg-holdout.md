# SDG Holdout Dataset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent data leakage in the evaluation pipeline by having SDG over-generate by `holdout_size` rows and persist the extra rows as a separate child `Dataset` (`parent_dataset_id` linked). Users then point `POST /evaluations` at the holdout dataset for a truly hold-out judge score.

**Architecture:**
- Add `holdout_size: int = 100` to `SDGRequest`. Worker calls the generator with `num_samples + holdout_size` as the effective target, then splits the result into 2 buckets.
- New pure-Python module `ai_engine/data_gen/holdout_split.py` does stratified split for classification (by label) and tool_calling (by tool name), random split for qa.
- New nullable column `datasets.parent_dataset_id` (self-FK, `ON DELETE CASCADE`) marks a child holdout dataset. Same `source=sdg` for both; `generation_metadata.role` (`"train"` / `"holdout"`) distinguishes them.
- Existing `POST /evaluations` works unchanged — user passes the child `dataset_id` for evaluation, parent `dataset_id` for training. No new endpoint needed.

**Tech Stack:** Python 3.11+ · Pydantic v2 · SQLAlchemy 2.0 async · Alembic · pytest · Celery (sync task body, async inner via `asyncio.run`)

---

## File Structure (touched by this plan)

| File | Action | Responsibility |
|------|--------|----------------|
| `ai_engine/data_gen/holdout_split.py` | **Create** | Pure-Python `split_rows(rows, task_type, holdout_size, rng)` — stratified for cls/tool, random for qa |
| `tests/unit/test_holdout_split.py` | **Create** | Unit tests for the splitter — covers all 3 task types, edge cases (0, > total, single bucket) |
| `alembic/versions/20260513_0003_dataset_parent_id.py` | **Create** | Add `datasets.parent_dataset_id` UUID nullable + FK + index |
| `api/models/dataset.py` | **Modify** | Add `parent_dataset_id` + relationship `parent` + `holdout_children` |
| `api/schemas/sdg.py` | **Modify** | Add `holdout_size: int = 100, ge=0, le=2000` to `_SDGRequestBase` + example payloads |
| `api/schemas/datasets.py` | **Modify** | Expose `parent_dataset_id: UUID \| None` on `DatasetResponse` |
| `workers/tasks/data_generation.py` | **Modify** | Over-generate, call `split_rows`, persist 2 JSONL + 2 Dataset rows, include `holdout_dataset_id` in `JobCompleted.result` |
| `tests/unit/test_sdg_schema.py` | **Create** | Validates `holdout_size` field bounds + default — new file (none exists today) |
| `docs/runbooks/api_docs.md` | **Modify** | Document `holdout_size` field + `parent_dataset_id` response field |
| `WORKING_LOG.md` | **Modify** | Log new session at top |
| `TASK_TRACKER.md` | **Modify** | Add new phase entry (Phase 11 or extend Phase 10) |

---

## Task 0: Branch setup

**Files:**
- None (git operation only)

- [ ] **Step 1: Confirm starting point**

Run: `git status -sb && git log --oneline -3`
Expected: clean working tree on `feature/training-eval-smoke-v2` OR untracked-only state. The 6 untracked `scripts/session*.py` files are unrelated — leave them.

- [ ] **Step 2: Create feature branch**

Run: `git checkout -b feature/sdg-holdout`
Expected: `Switched to a new branch 'feature/sdg-holdout'`

- [ ] **Step 3: Verify tests pass on the baseline**

Run: `pytest tests/unit -q`
Expected: all pass. (If any pre-existing failures, note them — we won't fix unrelated tests in this branch.)

---

## Task 1: Holdout split module (TDD)

**Files:**
- Create: `ai_engine/data_gen/holdout_split.py`
- Test: `tests/unit/test_holdout_split.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_holdout_split.py`:

```python
"""Unit tests for holdout split (classification stratified, tool stratified, qa random)."""

from __future__ import annotations

import json
import random
from collections import Counter

import pytest

from ai_engine.data_gen.holdout_split import split_rows
from api.schemas.enums import TaskType


def _seeded_rng() -> random.Random:
    return random.Random(42)


def test_qa_random_split_sizes_exact():
    rows = [{"question": f"q{i}", "answer": f"a{i}"} for i in range(50)]
    train, holdout = split_rows(rows, TaskType.QA, holdout_size=10, rng=_seeded_rng())
    assert len(train) == 40
    assert len(holdout) == 10
    # No row appears in both
    train_qs = {r["question"] for r in train}
    holdout_qs = {r["question"] for r in holdout}
    assert train_qs.isdisjoint(holdout_qs)
    # Union equals input
    assert train_qs | holdout_qs == {r["question"] for r in rows}


def test_holdout_size_zero_returns_all_as_train():
    rows = [{"question": f"q{i}", "answer": f"a{i}"} for i in range(5)]
    train, holdout = split_rows(rows, TaskType.QA, holdout_size=0, rng=_seeded_rng())
    assert len(train) == 5
    assert holdout == []


def test_holdout_size_clamped_when_larger_than_total():
    rows = [{"question": f"q{i}", "answer": f"a{i}"} for i in range(3)]
    train, holdout = split_rows(rows, TaskType.QA, holdout_size=10, rng=_seeded_rng())
    # All 3 rows go to holdout; train is empty
    assert len(holdout) == 3
    assert train == []


def test_classification_stratified_preserves_label_proportions():
    rows = (
        [{"text": f"t{i}", "label": "A"} for i in range(60)]
        + [{"text": f"u{i}", "label": "B"} for i in range(30)]
        + [{"text": f"v{i}", "label": "C"} for i in range(10)]
    )
    train, holdout = split_rows(
        rows, TaskType.CLASSIFICATION, holdout_size=10, rng=_seeded_rng()
    )
    assert len(holdout) == 10
    assert len(train) == 90
    # Each label appears in holdout at roughly its overall ratio
    counts = Counter(r["label"] for r in holdout)
    # A=60% → ~6, B=30% → ~3, C=10% → ~1 (rounding may shift ±1)
    assert counts["A"] in (5, 6, 7)
    assert counts["B"] in (2, 3, 4)
    assert counts["C"] in (0, 1, 2)


def test_classification_no_label_loss_in_holdout_when_possible():
    # 4 labels each with 5 rows; holdout=4 should ideally cover all labels
    rows = [
        {"text": f"t-{lbl}-{i}", "label": lbl}
        for lbl in ["A", "B", "C", "D"]
        for i in range(5)
    ]
    train, holdout = split_rows(
        rows, TaskType.CLASSIFICATION, holdout_size=4, rng=_seeded_rng()
    )
    assert len(holdout) == 4
    labels_in_holdout = {r["label"] for r in holdout}
    # Stratified split should hit all 4 labels at this size
    assert labels_in_holdout == {"A", "B", "C", "D"}


def test_tool_calling_stratified_by_tool_name():
    rows = []
    for tool in ["play_music", "set_oven", "no_tool_needed"]:
        for i in range(10):
            rows.append({
                "question": f"q-{tool}-{i}",
                "answer": json.dumps({"name": tool, "parameters": {}}),
            })
    train, holdout = split_rows(
        rows, TaskType.TOOL_CALLING, holdout_size=6, rng=_seeded_rng()
    )
    assert len(holdout) == 6
    # Should pick ~2 from each of the 3 tools (30 total, 6 holdout, even split)
    holdout_tools = Counter(json.loads(r["answer"])["name"] for r in holdout)
    assert all(1 <= c <= 3 for c in holdout_tools.values())
    assert set(holdout_tools.keys()) == {"play_music", "set_oven", "no_tool_needed"}


def test_tool_calling_unparseable_answer_goes_to_fallback_bucket():
    rows = [
        {"question": "q1", "answer": json.dumps({"name": "play_music", "parameters": {}})},
        {"question": "q2", "answer": "not-valid-json"},
        {"question": "q3", "answer": json.dumps({"parameters": {}})},  # missing 'name'
    ]
    # Must not crash on bad rows; they get assigned to a fallback bucket
    train, holdout = split_rows(
        rows, TaskType.TOOL_CALLING, holdout_size=1, rng=_seeded_rng()
    )
    assert len(holdout) == 1
    assert len(train) == 2


def test_empty_input_returns_empty_pair():
    train, holdout = split_rows([], TaskType.QA, holdout_size=10, rng=_seeded_rng())
    assert train == []
    assert holdout == []


def test_deterministic_with_seeded_rng():
    rows = [{"text": f"t{i}", "label": "X"} for i in range(20)]
    train1, holdout1 = split_rows(
        rows, TaskType.CLASSIFICATION, holdout_size=5, rng=random.Random(123)
    )
    train2, holdout2 = split_rows(
        rows, TaskType.CLASSIFICATION, holdout_size=5, rng=random.Random(123)
    )
    assert [r["text"] for r in train1] == [r["text"] for r in train2]
    assert [r["text"] for r in holdout1] == [r["text"] for r in holdout2]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_holdout_split.py -v`
Expected: ALL tests fail with `ModuleNotFoundError: No module named 'ai_engine.data_gen.holdout_split'`

- [ ] **Step 3: Implement the module**

Create `ai_engine/data_gen/holdout_split.py`:

```python
"""Stratified holdout split for SDG-generated rows.

The SDG worker over-generates by `holdout_size` rows; this module slices the
result into (train, holdout) such that the holdout is a faithful sample of
the overall distribution:

  • classification → stratified by `label`
  • tool_calling   → stratified by tool `name` (parsed from JSON `answer`)
  • qa             → random (no structural strata)

Pure Python — no DB, no MinIO, no network. Designed to be unit-testable.
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
        # qa or unknown — single bucket, random
        shuffled = list(rows)
        rng.shuffle(shuffled)
        return shuffled[holdout_size:], shuffled[:holdout_size]

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[key_fn(row)].append(row)
    for bucket in buckets.values():
        rng.shuffle(bucket)

    total = len(rows)
    # First pass: proportional allocation per bucket (rounded).
    per_bucket: dict[str, int] = {}
    for key, bucket in buckets.items():
        share = round(holdout_size * len(bucket) / total)
        per_bucket[key] = min(share, len(bucket))

    # Adjust to hit holdout_size exactly. Diff can be positive or negative.
    allocated = sum(per_bucket.values())
    diff = holdout_size - allocated
    if diff != 0:
        # Sort buckets by descending size for stable adjustment order.
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
            # Safety: if we cycled the full list without progress, break.
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
    return None  # QA → single bucket = random


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
```

- [ ] **Step 4: Run test to verify all pass**

Run: `pytest tests/unit/test_holdout_split.py -v`
Expected: 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add ai_engine/data_gen/holdout_split.py tests/unit/test_holdout_split.py
git commit -m "feat(sdg): stratified train/holdout split module + 9 unit tests"
```

---

## Task 2: SDG schema — add `holdout_size` field (TDD)

**Files:**
- Modify: `api/schemas/sdg.py:68-91` (base class) + `api/schemas/sdg.py:100-115, 133-175` (example payloads)
- Test: `tests/unit/test_sdg_schema.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_sdg_schema.py`:

```python
"""Validate `holdout_size` field on SDGRequest (defaults, bounds)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from api.schemas.sdg import SDGRequest


_PROJECT_ID = str(uuid4())
_SEED_ID = str(uuid4())
_ADAPTER = TypeAdapter(SDGRequest)


def _qa_with_seed_base() -> dict:
    return {
        "sdg_mode": "with_seed",
        "project_id": _PROJECT_ID,
        "task_type": "qa",
        "task_description": "Answer policy questions",
        "num_samples": 50,
        "seed_dataset_id": _SEED_ID,
    }


def test_holdout_size_defaults_to_100_when_omitted():
    req = _ADAPTER.validate_python(_qa_with_seed_base())
    assert req.holdout_size == 100


def test_holdout_size_zero_is_valid():
    payload = _qa_with_seed_base() | {"holdout_size": 0}
    req = _ADAPTER.validate_python(payload)
    assert req.holdout_size == 0


def test_holdout_size_explicit_value():
    payload = _qa_with_seed_base() | {"holdout_size": 25}
    req = _ADAPTER.validate_python(payload)
    assert req.holdout_size == 25


def test_holdout_size_negative_rejected():
    payload = _qa_with_seed_base() | {"holdout_size": -1}
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python(payload)


def test_holdout_size_over_cap_rejected():
    payload = _qa_with_seed_base() | {"holdout_size": 2001}
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python(payload)


def test_holdout_size_present_on_description_only_mode():
    payload = {
        "sdg_mode": "description_only",
        "project_id": _PROJECT_ID,
        "task_type": "classification",
        "task_description": "Classify support tickets",
        "num_samples": 200,
        "holdout_size": 50,
        "classification_config": {"labels": ["billing", "technical"]},
    }
    req = _ADAPTER.validate_python(payload)
    assert req.holdout_size == 50
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_sdg_schema.py -v`
Expected: tests checking `holdout_size` should fail because the field doesn't exist yet. Specifically `test_holdout_size_defaults_to_100_when_omitted` fails with `AttributeError` or `req.holdout_size` raising `AttributeError`. The negative/over-cap tests pass vacuously today (extra field rejected by `extra="forbid"`).

- [ ] **Step 3: Add the field to `_SDGRequestBase`**

Edit `api/schemas/sdg.py`. Find the existing `_SDGRequestBase` class (around line 68):

```python
class _SDGRequestBase(BaseModel):
    """Shared fields for both SDG modes."""

    model_config = ConfigDict(extra="forbid")

    project_id: UUID = Field(..., description="Owning project.")
    task_type: TaskType
    task_description: str = Field(
        ...,
        min_length=10,
        description="Natural-language description of what the model should learn.",
    )
    num_samples: int = Field(
        ...,
        gt=0,
        le=10_000,
        description="How many synthetic rows to generate.",
    )
    temperature: float = Field(default=0.9, ge=0.0, le=2.0)
    dataset_name: str | None = Field(
        default=None,
        description="Display name for the resulting dataset; defaults to project + timestamp.",
    )
```

Add `holdout_size` between `num_samples` and `temperature`:

```python
class _SDGRequestBase(BaseModel):
    """Shared fields for both SDG modes."""

    model_config = ConfigDict(extra="forbid")

    project_id: UUID = Field(..., description="Owning project.")
    task_type: TaskType
    task_description: str = Field(
        ...,
        min_length=10,
        description="Natural-language description of what the model should learn.",
    )
    num_samples: int = Field(
        ...,
        gt=0,
        le=10_000,
        description="How many synthetic rows to generate.",
    )
    holdout_size: int = Field(
        default=100,
        ge=0,
        le=2_000,
        description=(
            "Extra rows generated beyond `num_samples`, persisted as a separate "
            "child Dataset (linked via parent_dataset_id) for hold-out evaluation. "
            "Set to 0 to disable. Stratified by label (classification) / tool "
            "name (tool_calling); random for QA."
        ),
    )
    temperature: float = Field(default=0.9, ge=0.0, le=2.0)
    dataset_name: str | None = Field(
        default=None,
        description="Display name for the resulting dataset; defaults to project + timestamp.",
    )
```

- [ ] **Step 4: Update the example payloads to include `holdout_size`**

In `SDGRequestWithSeed.model_config.json_schema_extra.examples` (around line 102), append `"holdout_size": 50,` to the QA example:

```python
"examples": [
    {
        "sdg_mode": "with_seed",
        "project_id": "00000000-0000-0000-0000-000000000001",
        "task_type": "qa",
        "task_description": "Answer questions about our 30-day return policy",
        "num_samples": 200,
        "holdout_size": 50,
        "temperature": 0.9,
        "seed_dataset_id": "00000000-0000-0000-0000-000000000099",
    }
]
```

In `SDGRequestDescriptionOnly.model_config.json_schema_extra.examples`, add `"holdout_size": 100,` to both example payloads (right after `num_samples`).

- [ ] **Step 5: Run all sdg tests to verify pass**

Run: `pytest tests/unit/test_sdg_schema.py -v`
Expected: 6 tests PASS.

Run: `pytest tests/unit -q`
Expected: all unit tests (previous + new) PASS.

- [ ] **Step 6: Commit**

```bash
git add api/schemas/sdg.py tests/unit/test_sdg_schema.py
git commit -m "feat(sdg): add holdout_size field to SDGRequest (default 100, 0-2000)"
```

---

## Task 3: Alembic migration — `datasets.parent_dataset_id`

**Files:**
- Create: `alembic/versions/20260513_0003_dataset_parent_id.py`

- [ ] **Step 1: Create the migration file**

Create `alembic/versions/20260513_0003_dataset_parent_id.py`:

```python
"""add datasets.parent_dataset_id for SDG holdout child datasets

Revision ID: 0003_dataset_parent_id
Revises: 0002_export_error
Create Date: 2026-05-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_dataset_parent_id"
down_revision: str | None = "0002_export_error"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "datasets",
        sa.Column("parent_dataset_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_datasets_parent_dataset_id_datasets",
        source_table="datasets",
        referent_table="datasets",
        local_cols=["parent_dataset_id"],
        remote_cols=["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_datasets_parent_dataset_id",
        "datasets",
        ["parent_dataset_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_datasets_parent_dataset_id", table_name="datasets")
    op.drop_constraint(
        "fk_datasets_parent_dataset_id_datasets",
        "datasets",
        type_="foreignkey",
    )
    op.drop_column("datasets", "parent_dataset_id")
```

- [ ] **Step 2: Verify the migration renders to SQL cleanly**

Run: `alembic upgrade head --sql 2>&1 | tail -40`
Expected: SQL shows `ALTER TABLE datasets ADD COLUMN parent_dataset_id UUID`, the `ADD CONSTRAINT fk_datasets_parent_dataset_id_datasets ...`, and `CREATE INDEX ix_datasets_parent_dataset_id ...`. No errors. (`--sql` is offline; it does not need a live DB.)

If you do not have a local Python venv with alembic installed, skip the local `--sql` check and rely on the worker container or `docker compose run --rm api alembic upgrade head --sql`. Document any deviation.

- [ ] **Step 3: Commit**

```bash
git add alembic/versions/20260513_0003_dataset_parent_id.py
git commit -m "feat(db): migration 0003 — datasets.parent_dataset_id for SDG holdouts"
```

---

## Task 4: ORM model — `Dataset.parent_dataset_id` + relationships

**Files:**
- Modify: `api/models/dataset.py`

- [ ] **Step 1: Add column + self-relationship to the model**

Edit `api/models/dataset.py`. Add the column after `generation_metadata` (line 51) and add relationships at the bottom (after `evaluation_runs`):

```python
class Dataset(Base, TimestampMixin):
    __tablename__ = "datasets"

    id: Mapped[UUID] = uuid_pk()
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    task_type: Mapped[TaskType] = mapped_column(
        pg_enum(TaskType, "task_type"),
        nullable=False,
        index=True,
    )
    source: Mapped[DatasetSource] = mapped_column(
        pg_enum(DatasetSource, "dataset_source"),
        nullable=False,
    )
    num_samples: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    storage_uri: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        doc="s3://{bucket}/{key} pointing into MinIO. Null until generation completes.",
    )
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    generation_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        doc="SDG context (teacher_model, sdg_mode, num_invalid_rows, ...) when source=sdg.",
    )
    parent_dataset_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        doc=(
            "If set, this is a holdout child of parent_dataset_id (SDG over-"
            "generation). `generation_metadata.role` carries 'train'|'holdout'."
        ),
    )

    project: Mapped["Project"] = relationship(back_populates="datasets")
    training_jobs: Mapped[list["TrainingJob"]] = relationship(back_populates="dataset")
    evaluation_runs: Mapped[list["EvaluationRun"]] = relationship(back_populates="dataset")
    parent: Mapped["Dataset | None"] = relationship(
        "Dataset",
        remote_side="Dataset.id",
        back_populates="holdout_children",
    )
    holdout_children: Mapped[list["Dataset"]] = relationship(
        "Dataset",
        back_populates="parent",
        cascade="all, delete-orphan",
    )
```

- [ ] **Step 2: Import smoke-test**

Run: `python -c "from api.models.dataset import Dataset; print(Dataset.__table__.columns.keys())"`
Expected: prints column list ending with `parent_dataset_id` (the new column appears).

If running this outside a venv that has SQLAlchemy installed, run inside the api container:
`docker compose exec -T api python -c "from api.models.dataset import Dataset; print(list(Dataset.__table__.columns.keys()))"`

- [ ] **Step 3: Commit**

```bash
git add api/models/dataset.py
git commit -m "feat(db): Dataset.parent_dataset_id + self-relationship for holdouts"
```

---

## Task 5: Expose `parent_dataset_id` on `DatasetResponse`

**Files:**
- Modify: `api/schemas/datasets.py`

- [ ] **Step 1: Read the existing schema to confirm the location**

Run: `cat api/schemas/datasets.py | head -50`
Expected: see `class DatasetResponse(BaseModel)` definition. Note its existing fields.

- [ ] **Step 2: Add `parent_dataset_id` field**

Edit `api/schemas/datasets.py`. In `DatasetResponse` add (alphabetically near `id` or grouped with FK fields):

```python
parent_dataset_id: UUID | None = Field(
    default=None,
    description=(
        "If set, this dataset is a holdout child of another dataset (created "
        "by SDG over-generation). Use the parent for training and this one "
        "for `POST /evaluations` to get a leak-free judge score."
    ),
)
```

If `Field` is not already imported, add it. If `UUID` is not imported, add `from uuid import UUID`.

- [ ] **Step 3: Verify schema parses**

Run: `python -c "from api.schemas.datasets import DatasetResponse; print('parent_dataset_id' in DatasetResponse.model_fields)"`
Expected: `True`

(Use the docker compose exec fallback if no local venv.)

- [ ] **Step 4: Commit**

```bash
git add api/schemas/datasets.py
git commit -m "feat(api): expose Dataset.parent_dataset_id on DatasetResponse"
```

---

## Task 6: Worker — over-generate, split, persist 2 datasets

**Files:**
- Modify: `workers/tasks/data_generation.py`

This is the meat of the change. The worker today persists exactly one Dataset. After this task it persists the parent (existing row, train rows) and creates a new child row (holdout rows).

- [ ] **Step 1: Modify `_run_generator` to accept an effective target override**

Edit `workers/tasks/data_generation.py`. Replace `_run_generator` (lines 213–242) with:

```python
async def _run_generator(
    *,
    request: SDGRequestWithSeed | SDGRequestDescriptionOnly,
    effective_target: int,
    seed_rows: list[dict[str, Any]],
    pdf_bytes: bytes | None,
    settings,
    progress_cb,
):
    """Set up async + sync clients, run the generator with an explicit target.

    `effective_target = num_samples + holdout_size` when holdout is requested;
    otherwise equals num_samples.
    """
    # The generator reads target from request.num_samples — give it a copy
    # with num_samples set to effective_target so its quota math (sentinel
    # ratio, per-label quota) scales proportionally.
    effective_request = request.model_copy(update={"num_samples": effective_target})

    sync_client = OpenRouterClient(
        api_key=settings.openrouter_api_key,
        teacher_model="placeholder/unused",
        http_referer=settings.openrouter_http_referer,
        app_title=settings.openrouter_app_title,
    )
    async with AsyncOpenRouterClient(
        api_key=settings.openrouter_api_key,
        http_referer=settings.openrouter_http_referer,
        app_title=settings.openrouter_app_title,
    ) as async_client:
        gen = SyntheticDataGenerator(async_client, sync_client)
        return await gen.generate(
            effective_request,
            seed_rows=seed_rows,
            pdf_bytes=pdf_bytes,
            progress_cb=progress_cb,
        )
```

- [ ] **Step 2: Modify the main task body to split + persist 2 datasets**

Replace the body of `generate_synthetic_data` (lines 60–207). The full replacement (the imports section at the top of the file does not change except where noted in Step 3):

```python
@celery_app.task(bind=True, name="sdg.generate", max_retries=0)
def generate_synthetic_data(
    self,
    *,
    request_payload: dict[str, Any],
    dataset_id: str,
) -> dict[str, Any]:
    """Run one SDG job (Phase 9). Async generator + quota + sentinel + judge.

    Args:
        request_payload: serialized `SDGRequest` (mode-discriminated).
        dataset_id: pre-created (parent) dataset row id (UUID string).
    """
    job_id: str = self.request.id
    settings = get_settings()
    request = _sdg_adapter.validate_python(request_payload)
    parent_uuid = UUID(dataset_id)
    holdout_size = request.holdout_size
    effective_target = request.num_samples + holdout_size

    with sync_redis_scope() as redis:

        def emit_progress(p: GenerationProgress) -> None:
            publish_ws_message(
                redis,
                job_id,
                SDGProgress(
                    job_id=job_id,
                    phase=p.phase,
                    samples_generated=p.samples_generated,
                    samples_target=p.samples_target,
                    samples_valid=p.samples_valid,
                    samples_rejected=p.samples_rejected,
                    duplicates_removed=p.duplicates_removed,
                    current_loop=p.current_loop,
                    judge_rejected=p.judge_rejected,
                    judge_parse_failures=p.judge_parse_failures,
                    dedup_rejected=p.dedup_rejected,
                ),
            )

        try:
            log.info(
                "SDG starting: job=%s dataset=%s task=%s mode=%s "
                "train_target=%d holdout=%d effective=%d",
                job_id,
                dataset_id,
                request.task_type.value,
                request.sdg_mode.value,
                request.num_samples,
                holdout_size,
                effective_target,
            )

            # ---- Resolve seed payload from MinIO ---------------------------
            seed_rows, pdf_bytes = _load_seed_payload(request, settings)

            # ---- Run async generator loop ---------------------------------
            result = asyncio.run(
                _run_generator(
                    request=request,
                    effective_target=effective_target,
                    seed_rows=seed_rows,
                    pdf_bytes=pdf_bytes,
                    settings=settings,
                    progress_cb=emit_progress,
                )
            )

            # ---- Split into (train_rows, holdout_rows) --------------------
            import random as _random

            from ai_engine.data_gen.holdout_split import split_rows

            rng = _random.Random(_split_seed(request, dataset_id))
            train_rows, holdout_rows = split_rows(
                result.valid_rows,
                request.task_type,
                holdout_size,
                rng=rng,
            )

            # ---- Persist train (parent dataset) to MinIO ------------------
            emit_progress(
                GenerationProgress(
                    phase="persisting",
                    samples_generated=len(result.valid_rows),
                    samples_target=effective_target,
                    samples_valid=len(result.valid_rows),
                    samples_rejected=result.rejected_count,
                    duplicates_removed=result.duplicate_count,
                )
            )
            minio = get_minio_client()
            bucket = settings.minio_datasets_bucket
            train_key = f"sdg/{dataset_id}.jsonl"
            train_size = put_jsonl(minio, bucket, train_key, train_rows)
            train_uri = s3_uri(bucket, train_key)

            # ---- Persist holdout child dataset (only if any holdout rows) -
            holdout_uuid: UUID | None = None
            holdout_uri: str | None = None
            holdout_size_bytes: int | None = None
            if holdout_rows:
                from uuid import uuid4

                holdout_uuid = uuid4()
                holdout_key = f"sdg/{holdout_uuid}.jsonl"
                holdout_size_bytes = put_jsonl(
                    minio, bucket, holdout_key, holdout_rows
                )
                holdout_uri = s3_uri(bucket, holdout_key)

            # ---- DB writes (parent + child) -------------------------------
            with session_scope() as session:
                parent = session.get(Dataset, parent_uuid)
                if parent is None:
                    raise RuntimeError(
                        f"Dataset {dataset_id} disappeared mid-generation"
                    )
                parent.num_samples = len(train_rows)
                parent.storage_uri = train_uri
                parent.size_bytes = train_size
                parent_meta = dict(parent.generation_metadata or {})
                parent_meta.update(
                    {
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                        "rejected_count": result.rejected_count,
                        "duplicate_count": result.duplicate_count,
                        "judge_rejected_count": result.judge_rejected_count,
                        "judge_parse_failures": result.judge_parse_failures,
                        "api_calls": result.api_calls,
                        "holdout_size_requested": holdout_size,
                        "holdout_size_actual": len(holdout_rows),
                        "role": "train",
                        "holdout_dataset_id": (
                            str(holdout_uuid) if holdout_uuid else None
                        ),
                    }
                )
                parent.generation_metadata = parent_meta

                if holdout_uuid is not None:
                    child = Dataset(
                        id=holdout_uuid,
                        project_id=parent.project_id,
                        name=f"{parent.name}-holdout",
                        task_type=parent.task_type,
                        source=DatasetSource.SDG,
                        num_samples=len(holdout_rows),
                        storage_uri=holdout_uri,
                        size_bytes=holdout_size_bytes,
                        parent_dataset_id=parent.id,
                        generation_metadata={
                            "role": "holdout",
                            "parent_dataset_id": str(parent.id),
                            "sdg_mode": request.sdg_mode.value,
                            "task_description": request.task_description,
                            "completed_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    session.add(child)

            # ---- Publish completion --------------------------------------
            publish_ws_message(
                redis,
                job_id,
                JobCompleted(
                    job_id=job_id,
                    result={
                        "samples_generated": len(train_rows),
                        "holdout_samples": len(holdout_rows),
                        "rejected_count": result.rejected_count,
                        "duplicate_count": result.duplicate_count,
                        "judge_rejected_count": result.judge_rejected_count,
                        "judge_parse_failures": result.judge_parse_failures,
                        "api_calls": result.api_calls,
                        "storage_uri": train_uri,
                        "holdout_storage_uri": holdout_uri,
                        "holdout_dataset_id": (
                            str(holdout_uuid) if holdout_uuid else None
                        ),
                        "size_bytes": train_size,
                    },
                    dataset_id=parent_uuid,
                ),
            )

            log.info(
                "SDG done: job=%s parent=%s holdout=%s train=%d holdout=%d "
                "rejected=%d dup=%d calls=%d",
                job_id,
                dataset_id,
                holdout_uuid,
                len(train_rows),
                len(holdout_rows),
                result.rejected_count,
                result.duplicate_count,
                result.api_calls,
            )

            return {
                "status": "completed",
                "dataset_id": dataset_id,
                "holdout_dataset_id": (
                    str(holdout_uuid) if holdout_uuid else None
                ),
                "samples_generated": len(train_rows),
                "holdout_samples": len(holdout_rows),
                "rejected_count": result.rejected_count,
                "duplicate_count": result.duplicate_count,
                "judge_rejected_count": result.judge_rejected_count,
                "storage_uri": train_uri,
                "holdout_storage_uri": holdout_uri,
            }

        except Exception as exc:
            log.exception("SDG task failed (job=%s)", job_id)
            try:
                publish_ws_message(
                    redis,
                    job_id,
                    JobFailed(
                        job_id=job_id,
                        error=str(exc) or repr(exc),
                        error_type=type(exc).__name__,
                    ),
                )
            except Exception:  # noqa: BLE001 — never let publish failure mask the original
                log.warning("failed to publish JobFailed message", exc_info=True)
            raise
```

- [ ] **Step 3: Add a `_split_seed` helper at the bottom of the file**

After `_load_seed_payload` (currently ends around line 288), add:

```python
def _split_seed(
    request: SDGRequestWithSeed | SDGRequestDescriptionOnly,
    dataset_id: str,
) -> int:
    """Deterministic split seed derived from request + dataset id.

    Keeping this deterministic means re-running the same SDG request against
    the same dataset_id produces the same train/holdout assignment — useful
    if a downstream step crashed and the operator needs to retry.
    """
    # Hash a stable string; modulo to a positive int for random.Random.
    raw = f"{dataset_id}|{request.task_type.value}|{request.holdout_size}"
    return abs(hash(raw)) % (2**31 - 1)
```

- [ ] **Step 4: Compile-check the worker module**

Run: `python -m py_compile workers/tasks/data_generation.py`
Expected: no output, exit 0.

(If running on host without deps, use `docker compose exec -T worker python -m py_compile workers/tasks/data_generation.py` after the worker image has the new files mounted.)

- [ ] **Step 5: Commit**

```bash
git add workers/tasks/data_generation.py
git commit -m "feat(sdg): over-generate by holdout_size, split, persist 2 datasets"
```

---

## Task 7: API docs + runbook updates

**Files:**
- Modify: `docs/runbooks/api_docs.md`

- [ ] **Step 1: Document the request field**

Open `docs/runbooks/api_docs.md`. Find the section that documents `POST /datasets/generate` request body. Add a row to its field table:

```markdown
| `holdout_size` | int | 100 | Extra rows to over-generate for a hold-out evaluation dataset. 0 disables. Range 0–2000. Persisted as a separate child Dataset linked via `parent_dataset_id`. Stratified by label (classification) / tool name (tool_calling); random for QA. |
```

- [ ] **Step 2: Document the response shape change**

Find where `JobCompleted` for SDG is documented (look for "SDG completion event" or similar). Add a note that `result` now contains:
- `samples_generated` — train row count (parent dataset)
- `holdout_samples` — int (0 when holdout_size=0)
- `holdout_dataset_id` — UUID string | null
- `holdout_storage_uri` — string | null

- [ ] **Step 3: Document the response field on Dataset list/get**

Find where `DatasetResponse` is documented. Add a row:

```markdown
| `parent_dataset_id` | UUID \| null | If set, this is a holdout child. Use the parent for training and this one for `POST /evaluations` to get a leak-free score. |
```

- [ ] **Step 4: Add a "FE Integration Pattern" note for holdout flow**

Append to the FE Integration Patterns section a 5-line example:

```markdown
### Hold-out evaluation flow (no data leakage)

1. `POST /datasets/generate` with `num_samples: 200, holdout_size: 100` → returns `dataset_id=D_parent`
2. Wait for completion. `GET /datasets?project_id=...` now shows two rows: `D_parent` (train, 200 rows) and a child with `parent_dataset_id=D_parent` (holdout, 100 rows).
3. `POST /trainings` with `dataset_id=D_parent` → train on the 200-row parent.
4. `POST /models/{id}/export format=gguf` → wait.
5. `POST /evaluations` with `dataset_id=<holdout child id>` → judge sees rows the model never trained on.
```

- [ ] **Step 5: Commit**

```bash
git add docs/runbooks/api_docs.md
git commit -m "docs(api): document SDG holdout_size + parent_dataset_id + FE pattern"
```

---

## Task 8: Session log + task tracker

**Files:**
- Modify: `WORKING_LOG.md`
- Modify: `TASK_TRACKER.md`

- [ ] **Step 1: Add a new session entry at the top of `WORKING_LOG.md`**

Open `WORKING_LOG.md`. After the header (line 7, `---`), insert above the current "Session 19" entry:

```markdown
## Session 20 — SDG hold-out split for leak-free evaluation (2026-05-13)

**Who:** Claude (Opus 4.7) + parks (developer)
**Status:** ✅ Feature `feature/sdg-holdout` ready for review

**Why & What:**
- During the "how does LLM judge work?" walkthrough we noted that the default UX has users pointing `POST /evaluations` at the same dataset they trained on — guaranteed leakage since Unsloth also internally eval-splits 10% from that set, and judge sees `expected` answers directly.
- Designed an over-generation flow: SDG generates `num_samples + holdout_size` rows in a single run; result is split into train (saved to parent dataset) and holdout (saved as new child Dataset with `parent_dataset_id` set). Stratified by label / tool name for cls + tool, random for QA. Dedup happens before the split (Phase 9 MinHashLSH path is untouched), so near-dup train→holdout leakage is prevented.
- `holdout_size` is a request field defaulting to 100; `0` disables to preserve the option of single-dataset workflows.

**Files touched:**
- New: `ai_engine/data_gen/holdout_split.py`, `tests/unit/test_holdout_split.py`, `tests/unit/test_sdg_schema.py`, `alembic/versions/20260513_0003_dataset_parent_id.py`
- Modified: `api/models/dataset.py`, `api/schemas/sdg.py`, `api/schemas/datasets.py`, `workers/tasks/data_generation.py`, `docs/runbooks/api_docs.md`

**Test Summary:**
- 9 unit tests on `split_rows` — all green (cls stratified, tool stratified, qa random, edge cases: 0, > total, empty, deterministic seed)
- 6 unit tests on `SDGRequest.holdout_size` field — all green
- Alembic offline render — clean DDL for `0003_dataset_parent_id`
- Live SDG smoke not yet run (next session: run the 3 task-specific SDG runbooks against vast.ai with `holdout_size=20` to verify end-to-end)

**Next Action:**
- Run live SDG smoke with `holdout_size > 0` on each task type, then verify a hold-out evaluation against the child dataset returns a sensible judge score.
- Open PR `feature/sdg-holdout` → `dev` once smoke is green.

**Blockers:** None.

---
```

- [ ] **Step 2: Add task tracker entries**

Open `TASK_TRACKER.md`. After the existing "Phase 10" table, add a new section:

```markdown
## Phase 11 — SDG Hold-out for Leak-Free Evaluation (Session 20+)

> Branch: `feature/sdg-holdout` (from `feature/training-eval-smoke-v2`). Adds
> over-generation + train/holdout split so `POST /evaluations` can run against
> rows the trained model never saw.

| ID | Task | Status | Next Step |
|----|------|--------|-----------|
| HO.1 | `holdout_split.py` + 9 unit tests | ✅ | Commit on `feature/sdg-holdout` |
| HO.2 | `SDGRequest.holdout_size` field + 6 unit tests | ✅ | Commit on `feature/sdg-holdout` |
| HO.3 | Alembic migration `0003_dataset_parent_id` | ✅ | Commit on `feature/sdg-holdout` |
| HO.4 | `Dataset.parent_dataset_id` ORM + self-relationship | ✅ | Commit on `feature/sdg-holdout` |
| HO.5 | `DatasetResponse.parent_dataset_id` exposed on API | ✅ | Commit on `feature/sdg-holdout` |
| HO.6 | Worker over-generates, splits, persists 2 datasets | ✅ | Commit on `feature/sdg-holdout` |
| HO.7 | api_docs.md + FE integration pattern | ✅ | Commit on `feature/sdg-holdout` |
| HO.8 | Live SDG smoke (cls + tool + qa) with `holdout_size>0` | ⏳ | Next session: run 3 SDG runbooks on vast.ai, capture metrics for each holdout |
| HO.9 | Open PR `feature/sdg-holdout` → `dev` | ⏳ | After HO.8 green |
```

- [ ] **Step 3: Commit**

```bash
git add WORKING_LOG.md TASK_TRACKER.md
git commit -m "docs: log Session 20 — SDG holdout feature complete, awaiting live smoke"
```

---

## Final verification

- [ ] **Step 1: Run all unit tests**

Run: `pytest tests/unit -q`
Expected: All unit tests pass (no regressions). New tests: 9 in `test_holdout_split.py` + 6 in `test_sdg_schema.py` = 15 new passing.

- [ ] **Step 2: Inspect the commit graph**

Run: `git log --oneline feature/training-eval-smoke-v2..HEAD`
Expected: 7 commits on `feature/sdg-holdout`:
1. `feat(sdg): stratified train/holdout split module + 9 unit tests`
2. `feat(sdg): add holdout_size field to SDGRequest (default 100, 0-2000)`
3. `feat(db): migration 0003 — datasets.parent_dataset_id for SDG holdouts`
4. `feat(db): Dataset.parent_dataset_id + self-relationship for holdouts`
5. `feat(api): expose Dataset.parent_dataset_id on DatasetResponse`
6. `feat(sdg): over-generate by holdout_size, split, persist 2 datasets`
7. `docs(api): document SDG holdout_size + parent_dataset_id + FE pattern`
8. `docs: log Session 20 — SDG holdout feature complete, awaiting live smoke`

(Eight commits total. Adjust order/messages if a step needed re-doing.)

- [ ] **Step 3: Hand off to live-smoke session**

Stop here. The next session (parks-driven, on vast.ai) runs the 3 SDG runbooks with `holdout_size=20` and confirms:
1. `POST /datasets/generate` returns 202.
2. After completion, `GET /datasets?project_id=...` shows a parent (train rows) and a child with `parent_dataset_id` set (holdout rows).
3. `POST /evaluations` with `dataset_id=<child>` runs and returns metrics + LLM judge score.

If all three pass, open the PR.

---

## Notes for the implementer

- **DRY/YAGNI:** Do NOT add a separate `DatasetSource.HOLDOUT` enum value — that needs a PG ENUM migration with much more risk for no UX gain. The `generation_metadata.role` + `parent_dataset_id IS NOT NULL` carry that information already.
- **TDD discipline:** Tasks 1 and 2 follow strict TDD (red → green). Tasks 3–7 are infrastructure where the regression check is "the live smoke in HO.8". Don't skip Task 8 (live smoke) — schema looking right is not the same as the worker actually splitting rows correctly.
- **Frequent commits:** one per task is appropriate. Do not squash before PR — the linear history shows the order of concerns.
- **No backward-compat shim needed.** This is a PoC; old callers' SDG runs will now produce a +100-row child dataset. That is the intended behavior change. Document, don't shim.
- **Cost note for parks:** with default `holdout_size=100`, OpenRouter spend on SDG goes up by `100 / num_samples` ratio. For typical `num_samples=200` that's +50%. To opt out, pass `holdout_size: 0`.
