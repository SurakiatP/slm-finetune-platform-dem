"""datasets.seed_dataset_id — promote seed lineage to a real column + 'uploaded' source

Context: `with_seed` SDG generation has always recorded which seed dataset it
was bootstrapped from, but only inside `generation_metadata` (a JSONB blob
keyed by `'seed_dataset_id'`) — there was no first-class column, no index,
and no FK, so "which datasets were generated from seed X" required a JSONB
scan and offered no referential-integrity guarantee. This migration adds a
real self-referential `datasets.seed_dataset_id` column mirroring the
existing `parent_dataset_id` pattern (see `20260513_0003_dataset_parent_id.py`)
— nullable, FK -> datasets.id ON DELETE SET NULL (a dataset used as a seed
can later be deleted without taking its generated children down with it —
unlike `parent_dataset_id`'s CASCADE, which relates a holdout child to its
train split, not a generation source to its output), plus an index for the
same "find all datasets generated from X" lookup.

The application layer (`api/services/sdg_service.py`) keeps writing the
JSONB key too (backward compatibility — existing readers of
`generation_metadata['seed_dataset_id']` keep working); this column is an
addition, not a replacement.

Backfill: existing rows only have the lineage in
`generation_metadata->>'seed_dataset_id'`, a free-text JSONB value with no
schema-level guarantee it is (a) shaped like a UUID at all, or (b) still
pointing at a dataset that exists — the referenced seed could have been
deleted since. The backfill UPDATE therefore guards on BOTH:
  1. a uuid-shape regex on the raw text value, and
  2. an EXISTS subquery confirming a `datasets` row with that id is
     actually present
before casting to `uuid` and writing it — a row failing either guard is
left with `seed_dataset_id` NULL rather than raising (bad or dangling JSONB
should degrade gracefully, not fail the migration).

Also adds `'uploaded'` to the `dataset_source` enum (Postgres type
`dataset_source`, backing `Dataset.source` / `DatasetSource`) for the new
"uploaded directly for training, not an SDG seed" source — see
`api/schemas/enums.py::DatasetSource.UPLOADED`. This migration does not
itself write or backfill any row to that value (no existing dataset can
retroactively be known to be "uploaded-not-a-seed" from data alone) —
deliberately so: `ALTER TYPE ... ADD VALUE` is only legal inside the same
transaction as other DDL/DML as long as the new value is not *used*
(compared against, inserted, etc.) in that same transaction, and Alembic
runs each migration in one transaction. Leaving the value unused here is
what keeps this migration legal in-transaction on Postgres 12+.

Revision ID: 0013_dataset_reuse
Revises: 0012_training_decouple
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_dataset_reuse"
down_revision: str | None = "0012_training_decouple"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# uuid-shape guard for the backfill: 8-4-4-4-12 hex digits, case-insensitive.
_UUID_REGEX = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


def upgrade() -> None:
    op.add_column(
        "datasets",
        sa.Column("seed_dataset_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_datasets_seed_dataset_id_datasets"),
        "datasets",
        "datasets",
        ["seed_dataset_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_datasets_seed_dataset_id"),
        "datasets",
        ["seed_dataset_id"],
    )

    # Backfill from the pre-existing JSONB key. Guarded by BOTH a uuid-shape
    # regex AND an EXISTS check against `datasets` itself, because the JSONB
    # value is free text that may (a) not be UUID-shaped at all, or (b) be
    # UUID-shaped but point at a dataset that has since been deleted -- in
    # either case the row is left with seed_dataset_id NULL rather than
    # erroring the migration.
    op.execute(
        sa.text(
            "UPDATE datasets "
            "SET seed_dataset_id = (generation_metadata->>'seed_dataset_id')::uuid "
            "WHERE generation_metadata->>'seed_dataset_id' IS NOT NULL "
            f"AND generation_metadata->>'seed_dataset_id' ~ '{_UUID_REGEX}' "
            "AND EXISTS ("
            "SELECT 1 FROM datasets d2 "
            "WHERE d2.id = (datasets.generation_metadata->>'seed_dataset_id')::uuid"
            ")"
        )
    )

    # New DatasetSource value for datasets uploaded directly for training
    # (not an SDG seed). Deliberately NOT used anywhere else in this
    # migration -- see module docstring for why that's what keeps this
    # legal to run in the same transaction as the DDL/backfill above.
    op.execute("ALTER TYPE dataset_source ADD VALUE IF NOT EXISTS 'uploaded'")


def downgrade() -> None:
    op.drop_index(op.f("ix_datasets_seed_dataset_id"), table_name="datasets")
    op.drop_constraint(
        op.f("fk_datasets_seed_dataset_id_datasets"),
        "datasets",
        type_="foreignkey",
    )
    op.drop_column("datasets", "seed_dataset_id")
    # Postgres cannot remove a value from an enum type (no `DROP VALUE`), so
    # 'uploaded' stays in `dataset_source` even after this downgrade. That is
    # a no-op in practice: nothing in this migration ever wrote it, and
    # leaving an unused-but-legal enum value behind is harmless.
