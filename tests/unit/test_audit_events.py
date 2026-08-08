"""Unit tests for the `audit_events` table, its schema, and `audit_service`.

Layers covered:
  1. Model-level     — column shape/nullability, the project_id FK's
                        ondelete="SET NULL".
  2. Migration-level — source-text guards on the 0008 migration: literal
                        revision/down_revision strings, "SET NULL" present,
                        and no new `sa.Enum(` type creation.
  3. Schema-level     — `AuditEventResponse` exposes the ORM's
                        `event_metadata` attribute as JSON key `metadata`.
  4. Service-level    — `record()` only `session.add()`s (no commit of its
                        own; the row survives the *caller's* commit, and is
                        gone if the caller rolls back instead), and
                        `list_activity()` orders newest-first and 403s for
                        an existing project belonging to a different user
                        (404 for a project that doesn't exist — ADR-012).

Runs entirely against an in-memory aiosqlite engine — no Postgres, no
Docker. Mirrors the `@compiles(JSONB, "sqlite")` shim from
`tests/unit/test_dataset_status.py` since `AuditEvent.event_metadata`
uses the same postgres-only JSONB type.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB

from api.core.auth import CurrentUser
from api.models.audit_event import AuditEvent
from api.models.base import Base
from api.models.project import Project
from api.schemas.audit import AuditEventResponse
from api.schemas.enums import TaskType
from api.services import audit_service


# ---- KNOWN GOTCHA shim: JSONB is Postgres-only, sqlite needs a stand-in ----


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


# =============================================================================
# 1. Model-level (no DB needed)
# =============================================================================


class TestAuditEventModelColumns:
    def test_id_is_primary_key(self) -> None:
        col = AuditEvent.__table__.columns["id"]
        assert col.primary_key is True

    def test_created_at_not_nullable_and_indexed(self) -> None:
        col = AuditEvent.__table__.columns["created_at"]
        assert col.nullable is False
        assert col.index is True

    def test_no_updated_at_column(self) -> None:
        # Deliberately not using TimestampMixin: audit rows are immutable.
        assert "updated_at" not in AuditEvent.__table__.columns

    def test_actor_id_nullable_and_indexed(self) -> None:
        col = AuditEvent.__table__.columns["actor_id"]
        assert col.nullable is True
        assert col.index is True
        assert col.type.length == 64

    def test_action_resource_type_outcome_not_nullable(self) -> None:
        assert AuditEvent.__table__.columns["action"].nullable is False
        assert AuditEvent.__table__.columns["resource_type"].nullable is False
        assert AuditEvent.__table__.columns["outcome"].nullable is False

    def test_resource_id_and_request_id_nullable(self) -> None:
        assert AuditEvent.__table__.columns["resource_id"].nullable is True
        assert AuditEvent.__table__.columns["request_id"].nullable is True
        assert AuditEvent.__table__.columns["request_id"].index is True

    def test_project_id_nullable_indexed_fk_set_null(self) -> None:
        col = AuditEvent.__table__.columns["project_id"]
        assert col.nullable is True
        assert col.index is True
        fks = list(col.foreign_keys)
        assert len(fks) == 1
        assert fks[0].column.table.name == "projects"
        assert fks[0].ondelete == "SET NULL"

    def test_metadata_column_name_vs_python_attribute(self) -> None:
        # DB column is literally "metadata"...
        assert "metadata" in AuditEvent.__table__.columns
        assert AuditEvent.__table__.columns["metadata"].nullable is True
        # ...but the mapped Python attribute must be `event_metadata`,
        # because `metadata` is reserved by DeclarativeBase.
        assert hasattr(AuditEvent, "event_metadata")
        assert AuditEvent.__mapper__.get_property("event_metadata").columns[0].name == "metadata"

    def test_composite_index_on_project_id_created_at(self) -> None:
        composite = [
            ix
            for ix in AuditEvent.__table__.indexes
            if {c.name for c in ix.columns} == {"project_id", "created_at"}
        ]
        assert len(composite) == 1

    def test_outcome_is_plain_string_not_enum(self) -> None:
        import sqlalchemy as sa

        assert not isinstance(AuditEvent.__table__.columns["outcome"].type, sa.Enum)


class TestModelsPackageImports:
    def test_api_models_imports_cleanly_and_registers_audit_event(self) -> None:
        # Re-import to exercise the actual package __init__ path (not just
        # the module already imported above) and prove no
        # InvalidRequestError ("metadata" attribute collision) is raised.
        mod = importlib.import_module("api.models")
        importlib.reload(mod)
        assert mod.AuditEvent is AuditEvent
        assert "AuditEvent" in mod.__all__


# =============================================================================
# 2. Migration-level source guards (no DB needed)
# =============================================================================


class TestMigrationSourceGuards:
    _PATH = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260806_0008_audit_events.py"
    )

    def test_migration_file_exists(self) -> None:
        assert self._PATH.is_file()

    def test_revision_and_down_revision_literals(self) -> None:
        source = self._PATH.read_text()
        assert 'revision: str = "0008_audit_events"' in source
        assert 'down_revision: str | None = "0007_project_owner_id"' in source

    def test_set_null_present(self) -> None:
        source = self._PATH.read_text()
        assert "SET NULL" in source

    def test_no_new_enum_type_created(self) -> None:
        source = self._PATH.read_text()
        assert "sa.Enum(" not in source

    def test_downgrade_does_not_touch_job_status_enum(self) -> None:
        # `job_status` may appear in an explanatory comment (it does, in
        # this migration), but downgrade() must never issue an operation
        # against that shared enum type.
        source = self._PATH.read_text()
        assert "job_status_enum" not in source
        assert "DROP TYPE" not in source.upper()
        assert ".drop(" not in source


# =============================================================================
# 3. Schema-level (no DB needed)
# =============================================================================


class TestAuditEventResponseSchema:
    def test_event_metadata_attribute_serializes_as_metadata_key(self) -> None:
        from datetime import datetime, timezone

        event = AuditEvent(
            id=uuid4(),
            actor_id="user-1",
            project_id=uuid4(),
            action="training.cancel",
            resource_type="training_job",
            resource_id=str(uuid4()),
            outcome="success",
            request_id="req-1",
            event_metadata={"reason": "user requested"},
        )
        event.created_at = datetime.now(timezone.utc)

        resp = AuditEventResponse.model_validate(event)
        dumped = resp.model_dump(mode="json")

        assert "metadata" in dumped
        assert dumped["metadata"] == {"reason": "user requested"}
        assert "event_metadata" not in dumped


# =============================================================================
# 4. Service-level (in-memory aiosqlite)
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


async def _seed_project(session: AsyncSession, *, owner_id: str | None) -> Project:
    project = Project(id=uuid4(), name="proj", task_type=TaskType.QA, owner_id=owner_id)
    session.add(project)
    await session.flush()
    return project


class TestRecord:
    async def test_record_only_adds_no_commit(self, async_session: AsyncSession) -> None:
        project = await _seed_project(async_session, owner_id="user-1")
        await async_session.commit()

        event = audit_service.record(
            async_session,
            action="project.view",
            resource_type="project",
            resource_id=str(project.id),
            project_id=project.id,
            actor_id="user-1",
        )
        # record() only add()s — the uuid4() PK default is Python-side but
        # still only resolves at flush/insert time, so `event.id` is not
        # populated yet. The object being pending (not yet flushed) is
        # itself evidence record() didn't flush or commit.
        assert event in async_session.new

        # No commit happened inside record(): rolling back the caller's
        # session must discard the row entirely.
        await async_session.rollback()

        async with async_sessionmaker(
            async_session.bind, expire_on_commit=False, class_=AsyncSession
        )() as fresh:
            from sqlalchemy import select

            rows = (await fresh.execute(select(AuditEvent))).scalars().all()
            assert rows == []

    async def test_record_row_survives_caller_commit(self, async_session: AsyncSession) -> None:
        project = await _seed_project(async_session, owner_id="user-1")
        await async_session.commit()

        audit_service.record(
            async_session,
            action="project.view",
            resource_type="project",
            resource_id=str(project.id),
            project_id=project.id,
            actor_id="user-1",
            metadata={"k": "v"},
        )
        # Caller (not record()) is responsible for committing.
        await async_session.commit()

        async with async_sessionmaker(
            async_session.bind, expire_on_commit=False, class_=AsyncSession
        )() as fresh:
            from sqlalchemy import select

            rows = (await fresh.execute(select(AuditEvent))).scalars().all()
            assert len(rows) == 1
            assert rows[0].actor_id == "user-1"
            assert rows[0].event_metadata == {"k": "v"}

    async def test_record_raises_on_missing_required_field(
        self, async_session: AsyncSession
    ) -> None:
        # No try/except inside record(): a DB-level failure (here, a NOT
        # NULL violation surfaced at flush/commit time) must propagate.
        audit_service.record(
            async_session,
            action="x",
            resource_type=None,  # type: ignore[arg-type]
        )
        with pytest.raises(Exception):
            await async_session.commit()


class TestListActivity:
    async def test_returns_newest_first(self, async_session: AsyncSession) -> None:
        from datetime import datetime, timedelta, timezone

        project = await _seed_project(async_session, owner_id="user-1")
        await async_session.commit()

        # sqlite's CURRENT_TIMESTAMP (the server_default) only has
        # second-level resolution, so relying on it would make ordering a
        # coin flip across three rows inserted in the same instant. Set
        # created_at explicitly to make the newest-first ordering
        # unambiguous, independent of insertion/UUID order.
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for i in range(3):
            event = audit_service.record(
                async_session,
                action=f"action-{i}",
                resource_type="project",
                project_id=project.id,
                actor_id="user-1",
            )
            event.created_at = base + timedelta(seconds=i)
        await async_session.commit()

        user = CurrentUser(id="user-1", email=None)
        page = await audit_service.list_activity(
            async_session, project.id, limit=10, offset=0, user=user
        )

        assert page.total == 3
        assert [i.action for i in page.items] == ["action-2", "action-1", "action-0"]

    async def test_403_for_different_user(self, async_session: AsyncSession) -> None:
        """ADR-012: an existing project owned by someone else is 403, not
        the 404 this used to be."""
        project = await _seed_project(async_session, owner_id="user-1")
        await async_session.commit()

        audit_service.record(
            async_session,
            action="action-0",
            resource_type="project",
            project_id=project.id,
            actor_id="user-1",
        )
        await async_session.commit()

        other_user = CurrentUser(id="user-2", email=None)
        with pytest.raises(HTTPException) as excinfo:
            await audit_service.list_activity(
                async_session, project.id, limit=10, offset=0, user=other_user
            )
        assert excinfo.value.status_code == 403

    async def test_404_for_a_missing_project(self, async_session: AsyncSession) -> None:
        """Pair for the test above: a project id that names no row at all
        must stay 404, distinct from the 403 an existing-but-not-yours
        project now gets."""
        other_user = CurrentUser(id="user-2", email=None)
        with pytest.raises(HTTPException) as excinfo:
            await audit_service.list_activity(
                async_session, uuid4(), limit=10, offset=0, user=other_user
            )
        assert excinfo.value.status_code == 404

    async def test_none_user_is_noop_bypass(self, async_session: AsyncSession) -> None:
        # Matches ownership.py's documented "`user is None` is a complete
        # no-op" contract (phase-1 / auth-disabled compatibility).
        project = await _seed_project(async_session, owner_id="user-1")
        await async_session.commit()

        audit_service.record(
            async_session,
            action="action-0",
            resource_type="project",
            project_id=project.id,
        )
        await async_session.commit()

        page = await audit_service.list_activity(
            async_session, project.id, limit=10, offset=0, user=None
        )
        assert page.total == 1
