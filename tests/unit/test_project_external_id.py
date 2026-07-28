"""Unit tests proving the `Project.external_project_id` mapping end-to-end.

Layers covered (see class docstrings):
  1. Model-level    — column exists, nullable, unique-indexed.
  2. Schema-level    — `ProjectCreate` optional field, `ProjectResponse`
     round-trip (both a string value and `None`).
  3. Async service-level — `projects_service.create_project` (fresh id
     succeeds + persists, duplicate id raises 409, `None` never conflicts)
     and `projects_service.list_projects` (filter by `external_project_id`,
     and unfiltered default behaviour), against an in-memory aiosqlite
     engine.

Everything here runs against in-memory fakes only — no Postgres, no Docker,
no GPU, no real external calls.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB

from api.models.base import Base
from api.models.project import Project
from api.schemas.enums import TaskType
from api.schemas.projects import ProjectCreate, ProjectResponse
from api.services import projects_service


# ---- KNOWN GOTCHA shim: JSONB is Postgres-only, sqlite needs a stand-in ----
# `Project` rows can't be created without the full `Base.metadata`, which
# also includes `Dataset.generation_metadata` (a `postgresql.JSONB` column).
# Without this shim, `Base.metadata.create_all` fails to compile that
# column's type against the sqlite dialect. Same shim as
# `tests/unit/test_dataset_status.py`.


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


# =============================================================================
# 1. Model-level (no DB needed)
# =============================================================================


class TestProjectModelColumns:
    def test_external_project_id_column_exists_and_is_nullable(self) -> None:
        col = Project.__table__.columns["external_project_id"]
        assert col is not None
        assert col.nullable is True

    def test_external_project_id_has_unique_index(self) -> None:
        col = Project.__table__.columns["external_project_id"]
        # Column-level `.unique` flag is set...
        assert col.unique is True
        # ...and it's backed by an actual unique index on the table.
        matching = [
            idx
            for idx in Project.__table__.indexes
            if "external_project_id" in {c.name for c in idx.columns}
        ]
        assert len(matching) == 1
        assert matching[0].unique is True


# =============================================================================
# 2. Schema-level (no DB needed)
# =============================================================================


class TestProjectCreateSchema:
    def test_accepts_explicit_external_project_id(self) -> None:
        body = ProjectCreate(
            name="proj",
            task_type=TaskType.QA,
            external_project_id="ext-123",
        )
        assert body.external_project_id == "ext-123"

    def test_omitted_external_project_id_defaults_to_none(self) -> None:
        body = ProjectCreate(name="proj", task_type=TaskType.QA)
        assert body.external_project_id is None

    def test_explicit_none_is_accepted(self) -> None:
        body = ProjectCreate(
            name="proj", task_type=TaskType.QA, external_project_id=None
        )
        assert body.external_project_id is None


def _base_response_kwargs() -> dict:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return dict(
        id=uuid4(),
        name="proj",
        description=None,
        task_type=TaskType.QA,
        created_at=now,
        updated_at=now,
    )


class TestProjectResponseSchema:
    def test_round_trips_with_string_value(self) -> None:
        payload = {**_base_response_kwargs(), "external_project_id": "ext-abc"}
        resp = ProjectResponse.model_validate(payload)
        assert resp.external_project_id == "ext-abc"

    def test_round_trips_with_none(self) -> None:
        payload = {**_base_response_kwargs(), "external_project_id": None}
        resp = ProjectResponse.model_validate(payload)
        assert resp.external_project_id is None


# =============================================================================
# 3. Async service-level (in-memory aiosqlite)
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


class TestCreateProjectExternalId:
    async def test_fresh_external_project_id_persists(
        self, async_session: AsyncSession
    ) -> None:
        body = ProjectCreate(
            name="proj-a",
            task_type=TaskType.QA,
            external_project_id="ext-fresh",
        )
        resp = await projects_service.create_project(async_session, body)
        assert resp.external_project_id == "ext-fresh"

        got = await async_session.get(Project, resp.id)
        assert got is not None
        assert got.external_project_id == "ext-fresh"

    async def test_duplicate_external_project_id_raises_409(
        self, async_session: AsyncSession
    ) -> None:
        first = ProjectCreate(
            name="proj-a",
            task_type=TaskType.QA,
            external_project_id="ext-dup",
        )
        first_resp = await projects_service.create_project(async_session, first)

        second = ProjectCreate(
            name="proj-b",
            task_type=TaskType.CLASSIFICATION,
            external_project_id="ext-dup",
        )
        with pytest.raises(HTTPException) as excinfo:
            await projects_service.create_project(async_session, second)

        assert excinfo.value.status_code == 409
        assert "ext-dup" in str(excinfo.value.detail)
        assert str(first_resp.id) in str(excinfo.value.detail)

    async def test_none_external_project_id_never_conflicts(
        self, async_session: AsyncSession
    ) -> None:
        for i in range(3):
            body = ProjectCreate(name=f"proj-none-{i}", task_type=TaskType.QA)
            resp = await projects_service.create_project(async_session, body)
            assert resp.external_project_id is None


class TestListProjectsExternalIdFilter:
    async def _seed(self, async_session: AsyncSession) -> None:
        for body in [
            ProjectCreate(
                name="proj-foo", task_type=TaskType.QA, external_project_id="foo"
            ),
            ProjectCreate(
                name="proj-bar", task_type=TaskType.QA, external_project_id="bar"
            ),
            ProjectCreate(name="proj-none", task_type=TaskType.QA),
        ]:
            await projects_service.create_project(async_session, body)

    async def test_filters_to_exact_match(self, async_session: AsyncSession) -> None:
        await self._seed(async_session)

        page = await projects_service.list_projects(
            async_session, limit=50, offset=0, external_project_id="foo"
        )

        assert page.total == 1
        assert len(page.items) == 1
        assert page.items[0].external_project_id == "foo"

    async def test_no_filter_returns_all_seeded_projects(
        self, async_session: AsyncSession
    ) -> None:
        await self._seed(async_session)

        page = await projects_service.list_projects(
            async_session, limit=50, offset=0
        )

        assert page.total == 3
        assert len(page.items) == 3
