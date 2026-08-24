"""Unit tests for `PATCH /api/v1/datasets/{id}` (rename only).

Covers, per the endpoint's own contract (`api/schemas/datasets.py`'s
`DatasetUpdate`, `api/services/datasets_service.py`'s `rename_dataset`,
`api/routers/datasets.py`'s `update_dataset`):

  * a rename persists and the response echoes the new name
  * unknown dataset id -> 404
  * another owner's dataset -> 403 (ADR-012), via
    `ownership.assert_dataset_access` same as every other single-dataset
    endpoint
  * `DatasetUpdate`'s `extra="forbid"` rejects an unexpected field
    (`project_id`) with 422 rather than silently ignoring it
  * an empty `name` (violates `min_length=1`) -> 422
  * the rename writes a `dataset.rename` audit row
  * the route is wired into the app (`PATCH /api/v1/datasets/{dataset_id}`
    appears in `app.openapi()`)

Same in-memory aiosqlite + `@compiles(JSONB, "sqlite")` shim pattern as
`tests/unit/test_dataset_status.py` / `test_dataset_owner_access_matrix.py`
(`Dataset.generation_metadata` uses postgres-only JSONB).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from api.core.auth import CurrentUser
from api.models.audit_event import AuditEvent
from api.models.base import Base
from api.models.dataset import Dataset
from api.schemas.datasets import DatasetUpdate
from api.schemas.enums import DatasetSource, JobStatus, TaskType
from api.services import datasets_service


# ---- KNOWN GOTCHA shim: JSONB is Postgres-only, sqlite needs a stand-in ----
@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


USER_A = CurrentUser(id="user-a-sub", email="a@example.com")
USER_B = CurrentUser(id="user-b-sub", email="b@example.com")


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


async def _dataset(db: AsyncSession, *, owner: str | None, name: str = "orig-name") -> Dataset:
    dataset = Dataset(
        id=uuid4(),
        project_id=None,
        owner_id=owner,
        name=name,
        task_type=TaskType.QA,
        source=DatasetSource.SEED,
        status=JobStatus.COMPLETED,
        num_samples=1,
    )
    db.add(dataset)
    await db.commit()
    return dataset


# =============================================================================
# Service-level
# =============================================================================


class TestRenameDataset:
    async def test_rename_persists_and_echoes_new_name(self, db: AsyncSession) -> None:
        ds = await _dataset(db, owner=USER_A.id)

        resp = await datasets_service.rename_dataset(
            db, ds.id, DatasetUpdate(name="new-name"), USER_A
        )

        assert resp.name == "new-name"
        assert resp.id == ds.id

        got = await db.get(Dataset, ds.id)
        assert got is not None
        assert got.name == "new-name"

    async def test_404_unknown_id(self, db: AsyncSession) -> None:
        with pytest.raises(HTTPException) as exc:
            await datasets_service.rename_dataset(
                db, uuid4(), DatasetUpdate(name="whatever"), USER_A
            )
        assert exc.value.status_code == 404

    async def test_403_for_another_owners_dataset(self, db: AsyncSession) -> None:
        ds = await _dataset(db, owner=USER_A.id)

        with pytest.raises(HTTPException) as exc:
            await datasets_service.rename_dataset(
                db, ds.id, DatasetUpdate(name="hijacked"), USER_B
            )
        assert exc.value.status_code == 403

        # And the row must be untouched.
        got = await db.get(Dataset, ds.id)
        assert got is not None
        assert got.name == "orig-name"

    async def test_audit_row_written(self, db: AsyncSession) -> None:
        ds = await _dataset(db, owner=USER_A.id)

        await datasets_service.rename_dataset(
            db, ds.id, DatasetUpdate(name="renamed-for-audit"), USER_A
        )

        rows = (
            await db.execute(
                select(AuditEvent).where(AuditEvent.action == "dataset.rename")
            )
        ).scalars().all()
        assert len(rows) == 1
        event = rows[0]
        assert event.resource_type == "dataset"
        assert event.resource_id == str(ds.id)
        assert event.event_metadata == {
            "old_name": "orig-name",
            "new_name": "renamed-for-audit",
        }


# =============================================================================
# Schema-level (extra="forbid" / min_length=1)
# =============================================================================


class TestDatasetUpdateSchema:
    def test_extra_field_is_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            DatasetUpdate(name="ok", project_id=str(uuid4()))

    def test_empty_name_is_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            DatasetUpdate(name="")

    def test_overlong_name_is_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            DatasetUpdate(name="x" * 201)


# =============================================================================
# HTTP-level (422s, and the route's presence)
# =============================================================================


class TestUpdateDatasetOverHttp:
    @pytest.fixture
    def client_and_dataset(self):
        import asyncio

        from fastapi.testclient import TestClient

        from api.core.auth import require_user
        from api.core.database import get_db
        from api.main import app

        engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        state: dict = {"user": USER_A}

        async def _setup() -> Dataset:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with maker() as session:
                ds = await _dataset(session, owner=USER_A.id)
                return ds

        dataset = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            _setup()
        )

        async def _get_db():
            async with maker() as session:
                yield session

        app.dependency_overrides[get_db] = _get_db
        app.dependency_overrides[require_user] = lambda: state["user"]
        with TestClient(app) as client:
            yield client, dataset
        app.dependency_overrides.clear()

    def test_unexpected_field_is_422(self, client_and_dataset) -> None:
        client, dataset = client_and_dataset
        resp = client.patch(
            f"/api/v1/datasets/{dataset.id}",
            json={"project_id": str(uuid4())},
        )
        assert resp.status_code == 422

    def test_empty_name_is_422(self, client_and_dataset) -> None:
        client, dataset = client_and_dataset
        resp = client.patch(
            f"/api/v1/datasets/{dataset.id}",
            json={"name": ""},
        )
        assert resp.status_code == 422

    def test_valid_rename_returns_200_with_new_name(self, client_and_dataset) -> None:
        client, dataset = client_and_dataset
        resp = client.patch(
            f"/api/v1/datasets/{dataset.id}",
            json={"name": "renamed-via-http"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "renamed-via-http"


class TestRouteWiring:
    def test_patch_dataset_route_exists(self) -> None:
        from api.main import app

        paths = app.openapi()["paths"]
        assert "/api/v1/datasets/{dataset_id}" in paths
        assert "patch" in paths["/api/v1/datasets/{dataset_id}"]
