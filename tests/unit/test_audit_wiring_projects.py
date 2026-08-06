"""Audit wiring — projects, datasets, SDG, and the activity endpoint.

The contract being proven here is narrower and stricter than "a row gets
written": the audit INSERT rides the SAME transaction as the mutation it
records, so the two commit together or not at all. An audit log that can
silently miss entries when a write fails is not an audit log, and
`audit_service.record` deliberately has no try/except for that reason —
`TestAuditFailureRollsBackTheAction` is what pins it.

In-memory aiosqlite only — no Postgres, no MinIO, no broker.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from fastapi import HTTPException

from api.core import request_context
from api.core.auth import CurrentUser
from api.models.audit_event import AuditEvent
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.project import Project
from api.schemas.enums import DatasetSource, JobStatus, TaskType
from api.schemas.projects import ProjectCreate
from api.services import audit_service, projects_service


# ---- KNOWN GOTCHA shim: JSONB is Postgres-only, sqlite needs a stand-in ----
@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


USER_A = CurrentUser(id="user-a-sub", email="a@example.com")
USER_B = CurrentUser(id="user-b-sub", email="b@example.com")


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


async def _events(db: AsyncSession) -> list[AuditEvent]:
    return list((await db.execute(select(AuditEvent))).scalars().all())


# =============================================================================
# 1. Actions leave a trail
# =============================================================================


class TestProjectActions:
    async def test_create_records_one_event(self, db) -> None:
        resp = await projects_service.create_project(
            db, ProjectCreate(name="p1", task_type=TaskType.QA), USER_A
        )
        events = await _events(db)
        assert len(events) == 1
        assert events[0].action == "project.create"
        assert events[0].resource_id == str(resp.id)
        assert events[0].project_id == resp.id
        assert events[0].outcome == "success"

    async def test_delete_records_one_event_and_survives_the_delete(self, db) -> None:
        """The FK is ON DELETE SET NULL, so the row outlives its project —
        an audit trail a delete can erase would be worthless."""
        resp = await projects_service.create_project(
            db, ProjectCreate(name="p1", task_type=TaskType.QA), USER_A
        )
        await projects_service.delete_project(db, resp.id, USER_A)

        events = await _events(db)
        actions = [e.action for e in events]
        assert actions == ["project.create", "project.delete"]
        # The project is gone...
        assert (await db.get(Project, resp.id)) is None
        # ...and the trail is not.
        assert all(e.resource_id == str(resp.id) for e in events)

    async def test_actor_and_request_id_come_from_the_context(self, db) -> None:
        """Neither is threaded through the service signature — adding an audit
        call must never force a router change."""
        with request_context.bound(request_id="req-abc123", user_id=USER_A.id):
            resp = await projects_service.create_project(
                db, ProjectCreate(name="p1", task_type=TaskType.QA), USER_A
            )
        event = (await _events(db))[0]
        assert event.request_id == "req-abc123"
        assert event.actor_id == USER_A.id
        assert resp.owner_id == USER_A.id


# =============================================================================
# 2. THE contract: audit and action are one transaction
# =============================================================================


class TestAuditFailureRollsBackTheAction:
    async def test_a_failing_audit_insert_undoes_the_project(self, db, monkeypatch) -> None:
        """`record()` has no try/except on purpose. If it ever grows one, this
        test fails and the reason is: a swallowed audit error means the log
        develops gaps nobody can see, which defeats the whole feature."""

        def _boom(*a, **kw):
            raise RuntimeError("audit backend exploded")

        monkeypatch.setattr(audit_service, "record", _boom)

        with pytest.raises(RuntimeError):
            await projects_service.create_project(
                db, ProjectCreate(name="doomed", task_type=TaskType.QA), USER_A
            )
        await db.rollback()

        projects = (await db.execute(select(Project))).scalars().all()
        assert projects == [], "the project must not survive a failed audit write"
        assert await _events(db) == []

    async def test_record_does_not_commit_on_its_own(self, db) -> None:
        """It joins the caller's transaction rather than opening its own —
        that is what makes 'same transaction' true rather than aspirational."""
        audit_service.record(
            db, action="probe", resource_type="project", resource_id="x"
        )
        await db.rollback()
        assert await _events(db) == []


# =============================================================================
# 3. The activity endpoint
# =============================================================================


class TestActivityListing:
    async def _seed(self, db) -> Project:
        resp = await projects_service.create_project(
            db, ProjectCreate(name="p1", task_type=TaskType.QA), USER_A
        )
        project = await db.get(Project, resp.id)
        ds = Dataset(
            id=uuid4(),
            project_id=project.id,
            name="ds",
            task_type=TaskType.QA,
            source=DatasetSource.SEED,
            status=JobStatus.COMPLETED,
            num_samples=1,
        )
        db.add(ds)
        audit_service.record(
            db,
            action="dataset.seed_upload",
            resource_type="dataset",
            resource_id=str(ds.id),
            project_id=project.id,
            actor_id=USER_A.id,
        )
        await db.commit()

        # sqlite's CURRENT_TIMESTAMP has second resolution, so rows written in
        # the same instant tie on created_at and fall back to the random-uuid
        # id tiebreak. Space them explicitly so the ordering assertion tests
        # `ORDER BY created_at DESC` rather than luck. (Postgres, where this
        # actually runs, has microsecond resolution and no such tie.)
        rows = sorted(await _events(db), key=lambda e: e.action)
        base = datetime(2026, 8, 6, 12, 0, 0, tzinfo=timezone.utc)
        for offset, event in enumerate(rows):  # dataset.* first, project.* second
            event.created_at = base + timedelta(seconds=offset)
        await db.commit()
        return project

    async def test_owner_sees_newest_first(self, db) -> None:
        project = await self._seed(db)
        page = await audit_service.list_activity(
            db, project.id, limit=50, offset=0, user=USER_A
        )
        assert page.total == 2
        assert [e.action for e in page.items] == [
            "project.create",
            "dataset.seed_upload",
        ]

    async def test_another_user_gets_404_not_an_empty_page(self, db) -> None:
        """404, not 403 and not []: an empty page would confirm the project
        exists, which is the id oracle ADR-009 rules out."""
        project = await self._seed(db)
        with pytest.raises(HTTPException) as exc:
            await audit_service.list_activity(
                db, project.id, limit=50, offset=0, user=USER_B
            )
        assert exc.value.status_code == 404

    async def test_pagination(self, db) -> None:
        project = await self._seed(db)
        page = await audit_service.list_activity(
            db, project.id, limit=1, offset=1, user=USER_A
        )
        assert page.total == 2
        assert len(page.items) == 1
        assert page.items[0].action == "dataset.seed_upload"


# =============================================================================
# 4. Coverage guard — every mutating service writes a trail
# =============================================================================


_AUDITED_SERVICES = (
    "projects_service",
    "datasets_service",
    "sdg_service",
    "job_reconcile",
)


@pytest.mark.parametrize("service", _AUDITED_SERVICES)
def test_service_records_audit_events(service: str) -> None:
    """Parametrized over a tuple rather than written out per file, so adding
    a service here extends the guard instead of duplicating it — the house
    pattern from test_worker_progress_frames.py::_CANCELLABLE_TASKS, which
    exists because a fix once landed in 3 of 5 files and the gap survived a
    release."""
    import importlib
    import pathlib

    mod = importlib.import_module(f"api.services.{service}")
    src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    assert "audit_service" in src, f"{service} imports no audit_service"
    assert "audit_service.record(" in src, f"{service} never records an audit event"
