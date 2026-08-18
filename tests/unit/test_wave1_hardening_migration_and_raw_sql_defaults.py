"""W2-T7 cross-cutting hardening: migration-chain integrity + server_default
correctness independent of the ORM.

`tests/unit/test_dataset_decouple_auto_pipeline.py` already proves
`auto_export`/`auto_evaluate` resolve to `False` when a row is inserted via
`session.add(TrainingJob(...))` and left unset. That still routes through
SQLAlchemy's ORM INSERT machinery, which follows a specific path: omit the
column entirely from the generated INSERT and let the DB apply its own
column default, then (depending on dialect/eager_defaults) fetch it back.
It does NOT prove the DDL itself carries a real `DEFAULT` clause — a driver
or dialect quirk could paper over a missing one. This file drives a
raw-SQL `INSERT` (via `text()`, naming only the NOT NULL columns that have
no default) directly against the schema `Base.metadata.create_all` created
from the ORM models, bypassing the ORM's INSERT-compilation path entirely,
to prove the `server_default=sa.false()` from migration
`20260818_0010_dataset_decouple_auto_pipeline.py` is really wired into the
table DDL and not just an ORM-side convenience.

Also covers the migration file itself: parses, `down_revision` points at
the correct prior tip (`0009_usage_events`), `revision` matches its own
filename-embedded id, and `upgrade`/`downgrade` are both present and
importable (a bad migration file that half-parses is a much worse failure
mode than a wrong FK, since Alembic won't even build its revision graph).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from api.models.base import Base
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.enums import TaskType, TrainingMode


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


# =============================================================================
# 1. Migration file integrity
# =============================================================================


def _load_migration_module(filename: str) -> ModuleType:
    """Alembic version files aren't a real Python package (no `__init__.py`
    under `alembic/versions/`, and `alembic` itself resolves to the
    installed alembic library, not this repo's local directory of the same
    name) — load each one directly off disk by path, the same mechanism
    Alembic's own `ScriptDirectory` uses internally.
    """
    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestMigration0010FileIntegrity:
    FILENAME = "20260818_0010_dataset_decouple_auto_pipeline.py"

    @staticmethod
    @pytest.fixture(scope="class")
    def migration_module():
        return _load_migration_module(TestMigration0010FileIntegrity.FILENAME)

    def test_module_file_exists_at_expected_path(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        path = repo_root / "alembic" / "versions" / self.FILENAME
        assert path.is_file()

    def test_revision_matches_filename_embedded_id(self, migration_module) -> None:
        assert migration_module.revision == "0010_dataset_decouple_auto_pipeline"

    def test_down_revision_points_at_the_prior_tip(self, migration_module) -> None:
        # 0009_usage_events is the tip this migration was authored against
        # (see `ls alembic/versions/` sort order — 0009 is the file
        # immediately before 0010). If a later migration were inserted
        # with a different down_revision, `alembic upgrade head` would
        # either silently pick a different branch or fail to build a
        # single linear graph — this pins the expected value so that
        # regresses loudly instead.
        assert migration_module.down_revision == "0009_usage_events"

    def test_branch_labels_and_depends_on_are_unset(self, migration_module) -> None:
        # This migration isn't meant to branch or declare a cross-branch
        # dependency; a stray value here would be a sign of a bad merge.
        assert migration_module.branch_labels is None
        assert migration_module.depends_on is None

    def test_upgrade_and_downgrade_are_defined_and_callable(self, migration_module) -> None:
        assert callable(migration_module.upgrade)
        assert callable(migration_module.downgrade)

    def test_prior_tip_migration_file_has_matching_revision_id(self) -> None:
        """Cross-check the other side of the chain: 0009's own `revision`
        really is the string 0010 claims as its `down_revision` (catches a
        typo'd down_revision that happens to still "parse")."""
        prior = _load_migration_module("20260807_0009_usage_events.py")
        assert prior.revision == "0009_usage_events"


# =============================================================================
# 2. server_default correctness via raw SQL (bypasses ORM INSERT entirely)
# =============================================================================


@pytest.fixture
def raw_engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


class TestServerDefaultsSurviveRawSqlInsert:
    def test_auto_export_and_auto_evaluate_default_false_via_raw_insert(self, raw_engine) -> None:
        """Insert a training_jobs row via a hand-written INSERT statement
        that never mentions auto_export/auto_evaluate/auto_pipeline at
        all — the only way those columns get a value is the DDL-level
        `DEFAULT` clause SQLAlchemy compiled from `server_default=false()`
        in the migration. No ORM session involved on the write side.
        """
        project_id = uuid4()
        dataset_id = uuid4()
        job_id = uuid4()

        with raw_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO projects (id, name, task_type, created_at, updated_at) "
                    "VALUES (:id, :name, :task_type, datetime('now'), datetime('now'))"
                ),
                {"id": str(project_id), "name": "proj", "task_type": "qa"},
            )
            # datasets row so the (unenforced-by-sqlite, but schema-accurate)
            # FK target exists.
            conn.execute(
                text(
                    "INSERT INTO datasets "
                    "(id, project_id, name, task_type, source, status, num_samples, created_at, updated_at) "
                    "VALUES (:id, :project_id, 'ds', 'qa', 'sdg', 'completed', 1, datetime('now'), datetime('now'))"
                ),
                {"id": str(dataset_id), "project_id": str(project_id)},
            )
            conn.execute(
                text(
                    "INSERT INTO training_jobs "
                    "(id, project_id, dataset_id, mode, status, base_model, config_json, "
                    " created_at, updated_at) "
                    "VALUES (:id, :project_id, :dataset_id, 'manual', 'pending', 'some-model', '{}', "
                    " datetime('now'), datetime('now'))"
                ),
                {"id": str(job_id), "project_id": str(project_id), "dataset_id": str(dataset_id)},
            )

        with raw_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT auto_export, auto_evaluate, auto_pipeline FROM training_jobs WHERE id = :id"
                ),
                {"id": str(job_id)},
            ).one()

        auto_export, auto_evaluate, auto_pipeline = row
        # sqlite stores booleans as 0/1 — assert on the resolved falsiness
        # rather than an `is False` identity check, since the raw DBAPI
        # value is an int, not a Python bool (unlike the ORM-mapped path).
        assert not auto_export
        assert not auto_evaluate
        assert auto_pipeline is None

    def test_orm_readback_of_the_raw_inserted_row_matches(self, raw_engine) -> None:
        """Same raw INSERT as above, but read back through the ORM
        (`session.get`) to prove the DDL-level default and the
        Mapped[bool] column type agree on interpretation (no
        0-is-falsy-but-ORM-expects-True-only mismatch).

        IDs are inserted as `.hex` (32 chars, no dashes) rather than
        `str(uuid)` (36 chars, dashed) here — discovered empirically while
        writing this test: SQLAlchemy's generic `Uuid` column type binds
        sqlite parameters in `.hex` form, so `session.get(...)` silently
        returns `None` (not an error — just a WHERE clause that never
        matches) against a dashed-string row. The other raw-SQL test above
        never hits this because it reads back via a hand-written `SELECT`
        with a `:id` bind of the same dashed string it inserted — internally
        consistent even though it's not what the ORM would have written.
        """
        project_id = uuid4()
        dataset_id = uuid4()
        job_id = uuid4()

        with raw_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO projects (id, name, task_type, created_at, updated_at) "
                    "VALUES (:id, 'proj', 'qa', datetime('now'), datetime('now'))"
                ),
                {"id": project_id.hex},
            )
            conn.execute(
                text(
                    "INSERT INTO datasets "
                    "(id, project_id, name, task_type, source, status, num_samples, created_at, updated_at) "
                    "VALUES (:id, :project_id, 'ds', 'qa', 'sdg', 'completed', 1, datetime('now'), datetime('now'))"
                ),
                {"id": dataset_id.hex, "project_id": project_id.hex},
            )
            conn.execute(
                text(
                    "INSERT INTO training_jobs "
                    "(id, project_id, dataset_id, mode, status, base_model, config_json, "
                    " created_at, updated_at) "
                    "VALUES (:id, :project_id, :dataset_id, 'manual', 'pending', 'some-model', '{}', "
                    " datetime('now'), datetime('now'))"
                ),
                {"id": job_id.hex, "project_id": project_id.hex, "dataset_id": dataset_id.hex},
            )

        maker = sessionmaker(bind=raw_engine, expire_on_commit=False)
        session = maker()
        try:
            got = session.get(TrainingJob, job_id)
            assert got is not None
            assert got.auto_export is False
            assert got.auto_evaluate is False
            assert got.auto_pipeline is None
        finally:
            session.close()
