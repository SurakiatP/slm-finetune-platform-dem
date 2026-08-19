"""Unit tests for `datasets.owner_id` (W1-T1) -- per-user ownership that
SURVIVES a dataset being orphaned from its project.

Layers covered:
  1. Model-level     -- column shape/nullability/index on Dataset.owner_id.
  2. Migration-level -- source-text guards on the 0011 migration: literal
                        revision/down_revision strings, the backfill UPDATE
                        text, and the orphans-stay-NULL comment.
  3. Round-trip       -- against an in-memory aiosqlite engine, Dataset rows
                        with owner_id=None and owner_id="user-a" both
                        persist and read back correctly.

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


class TestDatasetOwnerIdColumn:
    def test_owner_id_column_exists_nullable_string_64(self) -> None:
        col = Dataset.__table__.columns["owner_id"]
        assert col is not None
        assert col.nullable is True
        assert col.type.length == 64

    def test_owner_id_is_indexed(self) -> None:
        col = Dataset.__table__.columns["owner_id"]
        assert col.index is True

    def test_owner_id_has_no_foreign_key(self) -> None:
        # Identity comes entirely from Supabase; there is no local users
        # table to reference.
        col = Dataset.__table__.columns["owner_id"]
        assert len(col.foreign_keys) == 0

    def test_owner_id_index_present_in_table_indexes(self) -> None:
        matches = [
            ix
            for ix in Dataset.__table__.indexes
            if {c.name for c in ix.columns} == {"owner_id"}
        ]
        assert len(matches) == 1
        assert matches[0].name == "ix_datasets_owner_id"


# =============================================================================
# 2. Migration-level source guards (no DB needed)
# =============================================================================


class TestMigrationSourceGuards:
    _PATH = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260819_0011_dataset_owner_id.py"
    )

    def test_migration_file_exists(self) -> None:
        assert self._PATH.is_file()

    def test_revision_and_down_revision_literals(self) -> None:
        source = self._PATH.read_text()
        assert 'revision: str = "0011_dataset_owner_id"' in source
        assert 'down_revision: str | None = "0010_dataset_decouple"' in source

    def test_revision_id_fits_alembic_version_column(self) -> None:
        # alembic_version.version_num is varchar(32); the revision string
        # itself must fit.
        source = self._PATH.read_text()
        assert len("0011_dataset_owner_id") <= 32
        assert 'revision: str = "0011_dataset_owner_id"' in source

    def test_backfill_update_present(self) -> None:
        source = self._PATH.read_text()
        assert "UPDATE datasets SET owner_id = p.owner_id" in source
        assert "FROM projects p WHERE datasets.project_id = p.id" in source

    def test_orphans_stay_null_comment_present(self) -> None:
        source = self._PATH.read_text()
        assert "already orphaned" in source
        assert "NULL" in source
        assert "fails-closed" in source or "fails closed" in source.lower()

    def test_index_created_in_upgrade(self) -> None:
        source = self._PATH.read_text()
        assert 'op.f("ix_datasets_owner_id")' in source
        assert "op.create_index(" in source

    def test_downgrade_drops_index_then_column(self) -> None:
        source = self._PATH.read_text()
        downgrade_body = source.split("def downgrade()")[1]
        drop_index_pos = downgrade_body.find("op.drop_index(")
        drop_column_pos = downgrade_body.find('op.drop_column("datasets", "owner_id")')
        assert drop_index_pos != -1
        assert drop_column_pos != -1
        assert drop_index_pos < drop_column_pos


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


class TestDatasetOwnerIdRoundTrip:
    async def test_owner_id_none_round_trips(self, async_session: AsyncSession) -> None:
        project = Project(id=uuid4(), name="proj", task_type=TaskType.QA)
        async_session.add(project)
        await async_session.flush()

        dataset = Dataset(
            id=uuid4(),
            project_id=project.id,
            owner_id=None,
            name="ds-no-owner",
            task_type=TaskType.QA,
            source=DatasetSource.SEED,
            status=JobStatus.COMPLETED,
            num_samples=1,
        )
        async_session.add(dataset)
        await async_session.commit()

        got = await async_session.get(Dataset, dataset.id)
        assert got is not None
        assert got.owner_id is None

    async def test_owner_id_user_a_round_trips(self, async_session: AsyncSession) -> None:
        project = Project(id=uuid4(), name="proj", owner_id="user-a", task_type=TaskType.QA)
        async_session.add(project)
        await async_session.flush()

        dataset = Dataset(
            id=uuid4(),
            project_id=project.id,
            owner_id="user-a",
            name="ds-with-owner",
            task_type=TaskType.QA,
            source=DatasetSource.SEED,
            status=JobStatus.COMPLETED,
            num_samples=1,
        )
        async_session.add(dataset)
        await async_session.commit()

        got = await async_session.get(Dataset, dataset.id)
        assert got is not None
        assert got.owner_id == "user-a"

    async def test_owner_id_survives_orphaning(self, async_session: AsyncSession) -> None:
        # Simulates the whole point of this column: project_id gets nulled
        # out (as the DB's ondelete="SET NULL" would do on project delete),
        # but owner_id must remain untouched.
        project = Project(id=uuid4(), name="proj", owner_id="user-a", task_type=TaskType.QA)
        async_session.add(project)
        await async_session.flush()

        dataset = Dataset(
            id=uuid4(),
            project_id=project.id,
            owner_id="user-a",
            name="ds-orphaned",
            task_type=TaskType.QA,
            source=DatasetSource.SEED,
            status=JobStatus.COMPLETED,
            num_samples=1,
        )
        async_session.add(dataset)
        await async_session.commit()

        dataset.project_id = None
        await async_session.commit()

        got = await async_session.get(Dataset, dataset.id)
        assert got is not None
        assert got.project_id is None
        assert got.owner_id == "user-a"
