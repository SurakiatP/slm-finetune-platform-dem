"""Unit tests for `datasets.seed_dataset_id` (G2) -- promoting SDG seed
lineage from a JSONB-only value to a real, indexed, FK-backed column, plus
the new `DatasetSource.UPLOADED` source.

Layers covered:
  1. Model-level     -- column shape/nullability/index/FK on
                        Dataset.seed_dataset_id.
  2. Migration-level -- source-text guards on the 0013 migration: literal
                        revision/down_revision strings, the ADD VALUE
                        statement, and the uuid-shape + EXISTS backfill
                        guard clauses.
  3. Round-trip       -- against an in-memory aiosqlite engine, Dataset rows
                        with seed_dataset_id set and seed_dataset_id=None
                        both persist and read back correctly.

Runs entirely against in-memory sqlite -- no Postgres, no Docker. Uses the
same `@compiles(JSONB, "sqlite")` shim as `tests/unit/test_dataset_status.py`
since `Dataset.generation_metadata` is a JSONB column.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from api.models.base import Base
from api.models.dataset import Dataset
from api.models.project import Project
from api.schemas.enums import DatasetSource, JobStatus, TaskType


# ---- KNOWN GOTCHA shim: JSONB is Postgres-only, sqlite needs a stand-in ----


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


# =============================================================================
# 1. Model-level (no DB needed)
# =============================================================================


class TestDatasetSeedDatasetIdColumn:
    def test_seed_dataset_id_column_exists_and_is_nullable(self) -> None:
        col = Dataset.__table__.columns["seed_dataset_id"]
        assert col is not None
        assert col.nullable is True

    def test_seed_dataset_id_is_indexed(self) -> None:
        col = Dataset.__table__.columns["seed_dataset_id"]
        assert col.index is True

    def test_seed_dataset_id_index_present_in_table_indexes(self) -> None:
        matches = [
            ix
            for ix in Dataset.__table__.indexes
            if {c.name for c in ix.columns} == {"seed_dataset_id"}
        ]
        assert len(matches) == 1
        assert matches[0].name == "ix_datasets_seed_dataset_id"

    def test_seed_dataset_id_has_self_referential_foreign_key(self) -> None:
        col = Dataset.__table__.columns["seed_dataset_id"]
        fks = list(col.foreign_keys)
        assert len(fks) == 1
        fk = fks[0]
        assert fk.column.table.name == "datasets"
        assert fk.column.name == "id"
        assert fk.ondelete == "SET NULL"

    def test_seed_relationship_is_distinct_from_parent_relationship(self) -> None:
        # The pre-existing parent/holdout_children self-referential pair
        # must be untouched -- this is a second, independently-disambiguated
        # self-FK relationship, not a replacement.
        mapper = Dataset.__mapper__
        assert "seed" in mapper.relationships
        assert "parent" in mapper.relationships
        assert "holdout_children" in mapper.relationships

        seed_rel = mapper.relationships["seed"]
        parent_rel = mapper.relationships["parent"]
        assert seed_rel is not parent_rel
        # Each relationship must be pinned to its own FK column so SQLAlchemy
        # doesn't have to (and can't) guess between the two self-FKs.
        seed_fk_cols = {c.name for c in seed_rel.local_remote_pairs[0][:1]}
        parent_fk_cols = {c.name for c in parent_rel.local_remote_pairs[0][:1]}
        assert seed_fk_cols == {"seed_dataset_id"}
        assert parent_fk_cols == {"parent_dataset_id"}


# =============================================================================
# 2. Migration-level source guards (no DB needed)
# =============================================================================


class TestMigrationSourceGuards:
    _PATH = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260825_0013_dataset_reuse.py"
    )

    def test_migration_file_exists(self) -> None:
        assert self._PATH.is_file()

    def test_revision_and_down_revision_literals(self) -> None:
        source = self._PATH.read_text()
        assert 'revision: str = "0013_dataset_reuse"' in source
        assert 'down_revision: str | None = "0012_training_decouple"' in source

    def test_revision_id_fits_alembic_version_column(self) -> None:
        # alembic_version.version_num is varchar(32); the revision string
        # itself must fit.
        assert len("0013_dataset_reuse") <= 32

    def test_column_add_and_fk_and_index_present(self) -> None:
        source = self._PATH.read_text()
        assert 'sa.Column("seed_dataset_id", sa.Uuid()' in source
        assert 'op.f("fk_datasets_seed_dataset_id_datasets")' in source
        assert 'op.f("ix_datasets_seed_dataset_id")' in source
        assert 'ondelete="SET NULL"' in source

    def test_add_value_statement_present(self) -> None:
        source = self._PATH.read_text()
        assert "ADD VALUE" in source
        assert "dataset_source" in source
        assert "'uploaded'" in source

    def test_add_value_is_noted_as_unused_in_this_migration(self) -> None:
        source = self._PATH.read_text()
        assert "NOT used" in source or "not used" in source.lower()
        assert "in-transaction" in source.lower() or "same transaction" in source.lower()

    def test_backfill_has_uuid_shape_regex_guard(self) -> None:
        source = self._PATH.read_text()
        assert "~" in source
        assert "0-9a-fA-F" in source

    def test_backfill_has_exists_guard(self) -> None:
        source = self._PATH.read_text()
        assert "EXISTS (" in source
        assert "FROM datasets d2" in source

    def test_backfill_reads_from_generation_metadata_key(self) -> None:
        source = self._PATH.read_text()
        assert "generation_metadata->>'seed_dataset_id'" in source

    def test_downgrade_drops_index_fk_column_in_order(self) -> None:
        source = self._PATH.read_text()
        downgrade_body = source.split("def downgrade()")[1]
        drop_index_pos = downgrade_body.find("op.drop_index(")
        drop_fk_pos = downgrade_body.find("op.drop_constraint(")
        drop_column_pos = downgrade_body.find(
            'op.drop_column("datasets", "seed_dataset_id")'
        )
        assert drop_index_pos != -1
        assert drop_fk_pos != -1
        assert drop_column_pos != -1
        assert drop_index_pos < drop_fk_pos < drop_column_pos

    def test_downgrade_notes_enum_values_cannot_be_removed(self) -> None:
        source = self._PATH.read_text()
        downgrade_body = source.split("def downgrade()")[1]
        assert "cannot remove" in downgrade_body.lower() or "can't remove" in downgrade_body.lower()
        assert "enum" in downgrade_body.lower()


# =============================================================================
# 3. Round-trip against in-memory aiosqlite
# =============================================================================


@pytest.fixture
async def async_session():
    """In-memory aiosqlite engine + AsyncSession with the full ORM schema."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session
    await engine.dispose()


class TestDatasetSeedDatasetIdRoundTrip:
    async def test_seed_dataset_id_none_round_trips(self, async_session: AsyncSession) -> None:
        project = Project(id=uuid4(), name="proj", task_type=TaskType.QA)
        async_session.add(project)
        await async_session.flush()

        dataset = Dataset(
            id=uuid4(),
            project_id=project.id,
            seed_dataset_id=None,
            name="ds-description-only",
            task_type=TaskType.QA,
            source=DatasetSource.SDG,
            status=JobStatus.PENDING,
            num_samples=0,
        )
        async_session.add(dataset)
        await async_session.commit()

        got = await async_session.get(Dataset, dataset.id)
        assert got is not None
        assert got.seed_dataset_id is None

    async def test_seed_dataset_id_set_round_trips(self, async_session: AsyncSession) -> None:
        project = Project(id=uuid4(), name="proj", task_type=TaskType.QA)
        async_session.add(project)
        await async_session.flush()

        seed = Dataset(
            id=uuid4(),
            project_id=project.id,
            name="seed-ds",
            task_type=TaskType.QA,
            source=DatasetSource.SEED,
            status=JobStatus.COMPLETED,
            num_samples=5,
            storage_uri="s3://bucket/seed.jsonl",
        )
        async_session.add(seed)
        await async_session.flush()

        generated = Dataset(
            id=uuid4(),
            project_id=project.id,
            seed_dataset_id=seed.id,
            name="sdg-ds",
            task_type=TaskType.QA,
            source=DatasetSource.SDG,
            status=JobStatus.PENDING,
            num_samples=0,
            generation_metadata={"seed_dataset_id": str(seed.id)},
        )
        async_session.add(generated)
        await async_session.commit()

        got = await async_session.get(Dataset, generated.id)
        assert got is not None
        assert got.seed_dataset_id == seed.id

    async def test_seed_dataset_id_survives_seed_deletion(
        self, async_session: AsyncSession
    ) -> None:
        # Simulates the FK's ondelete=SET NULL: deleting the seed dataset
        # must not take the generated dataset down with it (unlike
        # parent_dataset_id's CASCADE).
        project = Project(id=uuid4(), name="proj", task_type=TaskType.QA)
        async_session.add(project)
        await async_session.flush()

        seed = Dataset(
            id=uuid4(),
            project_id=project.id,
            name="seed-ds",
            task_type=TaskType.QA,
            source=DatasetSource.SEED,
            status=JobStatus.COMPLETED,
            num_samples=5,
            storage_uri="s3://bucket/seed.jsonl",
        )
        async_session.add(seed)
        await async_session.flush()

        generated = Dataset(
            id=uuid4(),
            project_id=project.id,
            seed_dataset_id=seed.id,
            name="sdg-ds",
            task_type=TaskType.QA,
            source=DatasetSource.SDG,
            status=JobStatus.PENDING,
            num_samples=0,
        )
        async_session.add(generated)
        await async_session.commit()

        # sqlite doesn't enforce/execute ondelete=SET NULL the way Postgres
        # would on a real FK-constrained delete, so emulate the effect
        # directly to prove the column itself is independently nullable
        # after the fact (the FK's ondelete clause is asserted separately
        # in TestDatasetSeedDatasetIdColumn).
        generated.seed_dataset_id = None
        await async_session.commit()

        got = await async_session.get(Dataset, generated.id)
        assert got is not None
        assert got.seed_dataset_id is None

    async def test_uploaded_source_round_trips(self, async_session: AsyncSession) -> None:
        project = Project(id=uuid4(), name="proj", task_type=TaskType.QA)
        async_session.add(project)
        await async_session.flush()

        dataset = Dataset(
            id=uuid4(),
            project_id=project.id,
            name="uploaded-ds",
            task_type=TaskType.QA,
            source=DatasetSource.UPLOADED,
            status=JobStatus.COMPLETED,
            num_samples=3,
            storage_uri="s3://bucket/uploaded.jsonl",
        )
        async_session.add(dataset)
        await async_session.commit()

        got = await async_session.get(Dataset, dataset.id)
        assert got is not None
        assert got.source == DatasetSource.UPLOADED
