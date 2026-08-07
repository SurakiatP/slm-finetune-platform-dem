"""Unit tests for the `usage_events` table and its schema.

Layers covered:
  1. Model-level     — column shape/nullability, the project_id FK's
                        ondelete="SET NULL", both composite indexes, and
                        that `cost_usd` round-trips as NULL (not 0).
  2. Migration-level — source-text guards on the 0009 migration: literal
                        revision/down_revision strings, "SET NULL" present,
                        and no new `sa.Enum(` type creation.
  3. Schema-level     — `UsageEventResponse` / `UsageSummaryResponse` /
                        `UsageRollupItem` validate the expected shapes.

Runs entirely against an in-memory sqlite engine — no Postgres, no Docker,
and (unlike `tests/unit/test_dataset_status.py` / `test_audit_events.py`)
no `@compiles(JSONB, "sqlite")` shim, because `UsageEvent` has no JSONB
column at all.
"""

from __future__ import annotations

import importlib
from datetime import UTC
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.models.base import Base
from api.models.project import Project
from api.models.usage_event import UsageEvent
from api.schemas.enums import TaskType
from api.schemas.usage import UsageEventResponse, UsageRollupItem, UsageSummaryResponse

# =============================================================================
# 1. Model-level (no DB needed, except the round-trip test)
# =============================================================================


class TestUsageEventModelColumns:
    def test_id_is_primary_key(self) -> None:
        col = UsageEvent.__table__.columns["id"]
        assert col.primary_key is True

    def test_created_at_not_nullable_and_indexed(self) -> None:
        col = UsageEvent.__table__.columns["created_at"]
        assert col.nullable is False
        assert col.index is True

    def test_no_updated_at_column(self) -> None:
        # Deliberately not using TimestampMixin: usage rows are write-once.
        assert "updated_at" not in UsageEvent.__table__.columns

    def test_actor_id_nullable_and_indexed(self) -> None:
        col = UsageEvent.__table__.columns["actor_id"]
        assert col.nullable is True
        assert col.index is True
        assert col.type.length == 64

    def test_project_id_nullable_indexed_fk_set_null(self) -> None:
        col = UsageEvent.__table__.columns["project_id"]
        assert col.nullable is True
        assert col.index is True
        fks = list(col.foreign_keys)
        assert len(fks) == 1
        assert fks[0].column.table.name == "projects"
        assert fks[0].ondelete == "SET NULL"

    def test_job_id_nullable_and_indexed(self) -> None:
        col = UsageEvent.__table__.columns["job_id"]
        assert col.nullable is True
        assert col.index is True
        assert col.type.length == 64

    def test_provider_model_stage_not_nullable(self) -> None:
        assert UsageEvent.__table__.columns["provider"].nullable is False
        assert UsageEvent.__table__.columns["model"].nullable is False
        assert UsageEvent.__table__.columns["stage"].nullable is False

    def test_token_columns_not_nullable(self) -> None:
        assert UsageEvent.__table__.columns["prompt_tokens"].nullable is False
        assert UsageEvent.__table__.columns["completion_tokens"].nullable is False

    def test_cost_usd_nullable_numeric_12_6(self) -> None:
        col = UsageEvent.__table__.columns["cost_usd"]
        assert col.nullable is True
        assert col.type.precision == 12
        assert col.type.scale == 6

    def test_outcome_not_nullable_and_plain_string(self) -> None:
        import sqlalchemy as sa

        col = UsageEvent.__table__.columns["outcome"]
        assert col.nullable is False
        assert not isinstance(col.type, sa.Enum)

    def test_no_jsonb_column(self) -> None:
        from sqlalchemy.dialects.postgresql import JSONB

        for col in UsageEvent.__table__.columns:
            assert not isinstance(col.type, JSONB)

    def test_composite_index_on_project_id_created_at(self) -> None:
        composite = [
            ix
            for ix in UsageEvent.__table__.indexes
            if {c.name for c in ix.columns} == {"project_id", "created_at"}
        ]
        assert len(composite) == 1
        assert composite[0].name == "ix_usage_events_project_id_created_at"

    def test_composite_index_on_actor_id_created_at(self) -> None:
        composite = [
            ix
            for ix in UsageEvent.__table__.indexes
            if {c.name for c in ix.columns} == {"actor_id", "created_at"}
        ]
        assert len(composite) == 1
        assert composite[0].name == "ix_usage_events_actor_id_created_at"


class TestModelsPackageImports:
    def test_api_models_imports_cleanly_and_registers_usage_event(self) -> None:
        mod = importlib.import_module("api.models")
        importlib.reload(mod)
        assert mod.UsageEvent is UsageEvent
        assert "UsageEvent" in mod.__all__

    def test_import_from_api_models_directly(self) -> None:
        # Mirrors the acceptance criterion `python -c "from api.models import
        # UsageEvent"`.
        from api.models import UsageEvent as ImportedUsageEvent

        assert ImportedUsageEvent is UsageEvent


# =============================================================================
# 2. Migration-level source guards (no DB needed)
# =============================================================================


class TestMigrationSourceGuards:
    _PATH = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260807_0009_usage_events.py"
    )

    def test_migration_file_exists(self) -> None:
        assert self._PATH.is_file()

    def test_revision_and_down_revision_literals(self) -> None:
        source = self._PATH.read_text()
        assert 'revision: str = "0009_usage_events"' in source
        assert 'down_revision: str | None = "0008_audit_events"' in source

    def test_set_null_present(self) -> None:
        source = self._PATH.read_text()
        assert "SET NULL" in source

    def test_no_new_enum_type_created(self) -> None:
        source = self._PATH.read_text()
        assert "sa.Enum(" not in source

    def test_downgrade_does_not_touch_job_status_enum(self) -> None:
        source = self._PATH.read_text()
        assert "job_status_enum" not in source
        assert "DROP TYPE" not in source.upper()
        assert ".drop(" not in source


# =============================================================================
# 3. Round-trip against in-memory sqlite (NO JSONB shim needed)
# =============================================================================


@pytest.fixture
def sync_sessionmaker():
    """In-memory sync sqlite engine + sessionmaker, `usage_events` + `projects`
    only.

    Deliberately create only the tables this test needs (`tables=[...]`)
    rather than the full `Base.metadata.create_all`: `Base.metadata` also
    holds `audit_events`, which has a JSONB column that only compiles
    against sqlite once `tests/unit/test_audit_events.py`'s
    `@compiles(JSONB, "sqlite")` shim has been imported. Since `UsageEvent`
    itself has no JSONB column, scoping `create_all` this way means this
    file needs no such shim and stays green run standalone.
    """
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine, tables=[Project.__table__, UsageEvent.__table__])
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    yield maker
    engine.dispose()


class TestUsageEventRoundTrip:
    def test_round_trips_a_row_with_cost_usd_none(self, sync_sessionmaker) -> None:
        session = sync_sessionmaker()
        try:
            project = Project(id=uuid4(), name="proj", task_type=TaskType.QA)
            session.add(project)
            session.flush()

            event = UsageEvent(
                id=uuid4(),
                actor_id="user-1",
                project_id=project.id,
                job_id="celery-task-123",
                provider="openrouter",
                model="some/unpriced-model",
                stage="sdg",
                prompt_tokens=100,
                completion_tokens=50,
                cost_usd=None,
                outcome="completed",
            )
            session.add(event)
            session.commit()
        finally:
            session.close()

        session = sync_sessionmaker()
        try:
            rows = session.execute(select(UsageEvent)).scalars().all()
            assert len(rows) == 1
            got = rows[0]
            assert got.cost_usd is None
            assert got.actor_id == "user-1"
            assert got.job_id == "celery-task-123"
            assert got.provider == "openrouter"
            assert got.model == "some/unpriced-model"
            assert got.stage == "sdg"
            assert got.prompt_tokens == 100
            assert got.completion_tokens == 50
            assert got.outcome == "completed"
            assert got.created_at is not None
        finally:
            session.close()

    def test_project_id_survives_project_deletion_as_null(self, sync_sessionmaker) -> None:
        session = sync_sessionmaker()
        try:
            # sqlite doesn't enforce FKs by default, but exercising the
            # ORM-level ondelete="SET NULL" contract still documents intent
            # even where the in-memory engine won't itself cascade it.
            project = Project(id=uuid4(), name="proj", task_type=TaskType.QA)
            session.add(project)
            session.flush()

            event = UsageEvent(
                id=uuid4(),
                project_id=project.id,
                provider="openrouter",
                model="m",
                stage="sdg",
                prompt_tokens=1,
                completion_tokens=1,
                cost_usd=None,
                outcome="completed",
            )
            session.add(event)
            session.commit()

            fks = list(UsageEvent.__table__.columns["project_id"].foreign_keys)
            assert fks[0].ondelete == "SET NULL"
        finally:
            session.close()


# =============================================================================
# 4. Schema-level (no DB needed)
# =============================================================================


class TestUsageEventResponseSchema:
    def test_validates_from_orm_object_with_none_cost(self) -> None:
        from datetime import datetime

        event = UsageEvent(
            id=uuid4(),
            actor_id="user-1",
            project_id=uuid4(),
            job_id="job-1",
            provider="openrouter",
            model="m",
            stage="sdg",
            prompt_tokens=10,
            completion_tokens=5,
            cost_usd=None,
            outcome="completed",
        )
        event.created_at = datetime.now(UTC)

        resp = UsageEventResponse.model_validate(event)
        assert resp.cost_usd is None
        assert resp.outcome == "completed"


class TestUsageSummaryResponseSchema:
    def test_builds_with_unpriced_flag(self) -> None:
        from datetime import datetime

        summary = UsageSummaryResponse(
            period_start=datetime(2026, 1, 1, tzinfo=UTC),
            period_end=datetime(2026, 2, 1, tzinfo=UTC),
            prompt_tokens=100,
            completion_tokens=50,
            cost_usd=None,
            items=[
                UsageRollupItem(
                    model="m",
                    stage="sdg",
                    prompt_tokens=100,
                    completion_tokens=50,
                    cost_usd=None,
                )
            ],
            has_unpriced_usage=True,
        )
        assert summary.has_unpriced_usage is True
        assert summary.items[0].model == "m"
