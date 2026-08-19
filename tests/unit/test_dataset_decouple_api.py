"""Unit tests for W2-T2: the API surface for dataset/project decoupling.

W1-T1 made `datasets.project_id` nullable (`ondelete=SET NULL`) so a
dataset survives its parent project's deletion as an "orphan". This file
covers the API-layer follow-through:

  1. `DatasetResponse` serializes `project_id` as nullable and exposes
     `parent_dataset_id`.
  2. `datasets_service.list_datasets` lists orphaned rows (project_id IS
     NULL) without a `project_id` filter, under the no-auth path.
  3. `datasets_service.delete_dataset` pre-checks for training references
     and raises 409 with the exact contractual detail string, instead of
     letting the `training_jobs.dataset_id` RESTRICT FK surface as a 500.
  4. `datasets_service.delete_dataset` still succeeds for an orphan dataset
     with no training/evaluation references.

Everything here runs against an in-memory aiosqlite engine — no Postgres,
no Docker, no GPU. Postgres JSONB needs the sqlite @compiles shim (same
gotcha documented in test_dataset_status.py / test_dataset_decouple_auto_pipeline.py)
since sqlite has no native JSONB type.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from api.models.base import Base
from api.models.dataset import Dataset
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.datasets import DatasetResponse
from api.schemas.enums import DatasetSource, JobStatus, TaskType, TrainingMode
from api.services import datasets_service


# ---- KNOWN GOTCHA shim: JSONB is Postgres-only, sqlite needs a stand-in ----


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


# =============================================================================
# 1. Schema-level (no DB needed)
# =============================================================================


def _base_response_kwargs(*, project_id) -> dict:
    now = datetime.now(timezone.utc)
    return dict(
        id=uuid4(),
        project_id=project_id,
        name="ds",
        task_type=TaskType.QA,
        source=DatasetSource.SDG,
        status=JobStatus.COMPLETED,
        error_message=None,
        num_samples=0,
        storage_uri=None,
        size_bytes=None,
        generation_metadata=None,
        celery_task_id=None,
        created_at=now,
        updated_at=now,
    )


class TestDatasetResponseNullableProjectId:
    def test_project_id_none_round_trips(self) -> None:
        payload = _base_response_kwargs(project_id=None)
        resp = DatasetResponse.model_validate(payload)
        assert resp.project_id is None

    def test_project_id_set_still_round_trips(self) -> None:
        pid = uuid4()
        payload = _base_response_kwargs(project_id=pid)
        resp = DatasetResponse.model_validate(payload)
        assert resp.project_id == pid

    def test_parent_dataset_id_is_exposed(self) -> None:
        parent_id = uuid4()
        payload = {**_base_response_kwargs(project_id=uuid4()), "parent_dataset_id": parent_id}
        resp = DatasetResponse.model_validate(payload)
        assert resp.parent_dataset_id == parent_id

    def test_parent_dataset_id_defaults_to_none(self) -> None:
        payload = _base_response_kwargs(project_id=uuid4())
        resp = DatasetResponse.model_validate(payload)
        assert resp.parent_dataset_id is None


# =============================================================================
# 2. Async service-level (in-memory aiosqlite)
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


async def _make_project(session: AsyncSession) -> Project:
    project = Project(id=uuid4(), name="proj", task_type=TaskType.QA)
    session.add(project)
    await session.flush()
    return project


async def _make_dataset(
    session: AsyncSession, *, project_id, storage_uri: str | None = None
) -> Dataset:
    dataset = Dataset(
        id=uuid4(),
        project_id=project_id,
        name="ds",
        task_type=TaskType.QA,
        source=DatasetSource.SEED,
        status=JobStatus.COMPLETED,
        num_samples=1,
        storage_uri=storage_uri,
    )
    session.add(dataset)
    await session.flush()
    return dataset


class TestListDatasetsIncludesOrphans:
    async def test_orphan_dataset_listed_without_project_filter(
        self, async_session: AsyncSession
    ) -> None:
        project = await _make_project(async_session)
        owned = await _make_dataset(async_session, project_id=project.id)
        orphan = await _make_dataset(async_session, project_id=None)
        await async_session.commit()

        # No `project_id` filter, no `user` (auth off) — mirrors GET
        # /datasets with neither query param, the no-auth path.
        page = await datasets_service.list_datasets(
            async_session, project_id=None, limit=50, offset=0, user=None
        )

        ids = {item.id for item in page.items}
        assert owned.id in ids
        assert orphan.id in ids
        orphan_item = next(item for item in page.items if item.id == orphan.id)
        assert orphan_item.project_id is None
        assert page.total == 2


class TestDeleteDataset:
    async def test_delete_training_referenced_dataset_returns_409_exact_detail(
        self, async_session: AsyncSession
    ) -> None:
        project = await _make_project(async_session)
        dataset = await _make_dataset(async_session, project_id=project.id)
        job = TrainingJob(
            id=uuid4(),
            project_id=project.id,
            dataset_id=dataset.id,
            mode=TrainingMode.MANUAL,
            status=JobStatus.PENDING,
            base_model="Qwen/Qwen2.5-0.5B",
            config_json={"epochs": 1},
        )
        async_session.add(job)
        await async_session.commit()

        with pytest.raises(HTTPException) as excinfo:
            await datasets_service.delete_dataset(async_session, dataset.id, user=None)

        assert excinfo.value.status_code == 409
        assert excinfo.value.detail == (
            f"Dataset {dataset.id} is in use by one or more trainings and "
            "cannot be deleted"
        )

        # The dataset must still exist — the delete was refused, not partially applied.
        still_there = await async_session.get(Dataset, dataset.id)
        assert still_there is not None

    async def test_delete_orphan_dataset_succeeds(
        self, async_session: AsyncSession, fake_minio, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # `fake_minio` fixture patches `workers.storage.get_minio_client`, but
        # `datasets_service.py` did `from workers.storage import
        # get_minio_client`, binding its own module-local name at import
        # time — patch the use-site directly (same gotcha noted in
        # test_dataset_status.py).
        monkeypatch.setattr(datasets_service, "get_minio_client", lambda: fake_minio)

        orphan = await _make_dataset(async_session, project_id=None, storage_uri=None)
        await async_session.commit()

        await datasets_service.delete_dataset(async_session, orphan.id, user=None)

        gone = await async_session.get(Dataset, orphan.id)
        assert gone is None
