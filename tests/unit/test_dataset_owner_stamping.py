"""Unit tests for W2-T3: `Dataset.owner_id` is stamped at every creation site.

`Dataset.owner_id` (String(64), nullable) was added so an orphaned dataset
(`project_id` set to NULL on project delete, see W1-T1) stays visible to the
user who owns it, even after `project_id` no longer points anywhere. It is
copied from the owning `Project.owner_id` at creation time. There are
exactly four `Dataset(...)` construction sites:

  1. `api/services/sdg_service.py::submit_sdg_job` — the SDG pending
     placeholder row, `owner_id=project.owner_id`.
  2. `api/services/datasets_service.py::_persist_jsonl_dataset` —
     `owner_id=project.owner_id`.
  3. `api/services/datasets_service.py::_persist_pdf_dataset` —
     `owner_id=project.owner_id`.
  4. `workers/tasks/data_generation.py`'s `generate_synthetic_data` task,
     holdout `child` row — `owner_id=parent.owner_id` (inherited from the
     parent Dataset row rather than a Project row, since no Project row is
     loaded in that code path; equivalent to the project's owner because
     the parent itself was stamped from the same project at its own
     creation).

One test class per creation path, covering: the persisted row carries the
project's (or parent's) `owner_id`; a null-owner project yields a
null-owner dataset (fails closed, doesn't invent an owner); and, for the
holdout child, that its `owner_id` matches the parent's even with no
Project row loaded for that task run, and survives the owning project
being deleted first.

Same aiosqlite in-memory + `@compiles(JSONB, "sqlite")` shim pattern as
`tests/unit/test_dataset_status.py`; the worker-level tests reuse the
`.apply()` + sync-sqlite + `fake_minio`/`fake_redis_pubsub` harness from
`tests/unit/test_sdg_holdout_name.py`.
"""

from __future__ import annotations

from contextlib import contextmanager
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from ai_engine.data_gen.generator import SDGRunResult
from ai_engine.data_gen.usage import STAGE_GENERATE
from api.core.config import get_settings
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.project import Project
from api.schemas.enums import DatasetSource, JobStatus, TaskType
from api.schemas.sdg import SDGRequestDescriptionOnly
from api.schemas.upload import FormatDetectionReport


# ---- KNOWN GOTCHA shim: JSONB is Postgres-only, sqlite needs a stand-in ----


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


_OWNER = "11111111-1111-1111-1111-111111111111"


# =============================================================================
# Shared async harness (submit_sdg_job, _persist_jsonl_dataset, _persist_pdf_dataset)
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


async def _make_project(session: AsyncSession, *, owner_id: str | None) -> Project:
    project = Project(id=uuid4(), name="proj", task_type=TaskType.QA, owner_id=owner_id)
    session.add(project)
    await session.flush()
    return project


# =============================================================================
# 1. sdg_service.submit_sdg_job — SDG pending placeholder row
# =============================================================================


class TestSubmitSdgJobOwnerStamping:
    async def test_owned_project_stamps_dataset_owner_id(
        self, async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from api.services import sdg_service

        project = await _make_project(async_session, owner_id=_OWNER)

        import workers.tasks.data_generation as dg_module

        fake_async_result = type("FakeAsyncResult", (), {"id": "fake-job-id"})()
        monkeypatch.setattr(
            dg_module.generate_synthetic_data,
            "apply_async",
            lambda **kwargs: fake_async_result,
        )

        request = SDGRequestDescriptionOnly(
            project_id=project.id,
            task_type=TaskType.QA,
            task_description="Answer questions about our return policy",
            num_samples=10,
            holdout_size=0,
        )

        response = await sdg_service.submit_sdg_job(async_session, request)

        got = await async_session.get(Dataset, response.dataset_id)
        assert got is not None
        assert got.owner_id == _OWNER

    async def test_null_owner_project_yields_null_owner_dataset(
        self, async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from api.services import sdg_service

        project = await _make_project(async_session, owner_id=None)

        import workers.tasks.data_generation as dg_module

        fake_async_result = type("FakeAsyncResult", (), {"id": "fake-job-id"})()
        monkeypatch.setattr(
            dg_module.generate_synthetic_data,
            "apply_async",
            lambda **kwargs: fake_async_result,
        )

        request = SDGRequestDescriptionOnly(
            project_id=project.id,
            task_type=TaskType.QA,
            task_description="Answer questions about our return policy",
            num_samples=10,
            holdout_size=0,
        )

        response = await sdg_service.submit_sdg_job(async_session, request)

        got = await async_session.get(Dataset, response.dataset_id)
        assert got is not None
        assert got.owner_id is None


# =============================================================================
# 2. datasets_service._persist_jsonl_dataset
# =============================================================================


def _fd_report() -> FormatDetectionReport:
    return FormatDetectionReport(
        ran=False,
        model_used=None,
        field_mapping={},
        rows_total=1,
        rows_canonicalised=1,
        rows_dropped=0,
        notes="already canonical",
    )


class TestPersistJsonlDatasetOwnerStamping:
    async def test_owned_project_stamps_dataset_owner_id(
        self, async_session: AsyncSession, fake_minio, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from api.services import datasets_service as ds_module

        monkeypatch.setattr(ds_module, "get_minio_client", lambda: fake_minio)

        project = await _make_project(async_session, owner_id=_OWNER)

        dataset = await ds_module._persist_jsonl_dataset(
            async_session,
            settings=get_settings(),
            project=project,
            task_type=TaskType.QA,
            name="seed-ds",
            valid_rows=[{"question": "q1", "answer": "a1"}],
            fd_report=_fd_report(),
        )

        assert dataset.owner_id == _OWNER
        got = await async_session.get(Dataset, dataset.id)
        assert got is not None
        assert got.owner_id == _OWNER

    async def test_null_owner_project_yields_null_owner_dataset(
        self, async_session: AsyncSession, fake_minio, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from api.services import datasets_service as ds_module

        monkeypatch.setattr(ds_module, "get_minio_client", lambda: fake_minio)

        project = await _make_project(async_session, owner_id=None)

        dataset = await ds_module._persist_jsonl_dataset(
            async_session,
            settings=get_settings(),
            project=project,
            task_type=TaskType.QA,
            name="seed-ds",
            valid_rows=[{"question": "q1", "answer": "a1"}],
            fd_report=_fd_report(),
        )

        assert dataset.owner_id is None


# =============================================================================
# 3. datasets_service._persist_pdf_dataset
# =============================================================================


class TestPersistPdfDatasetOwnerStamping:
    async def test_owned_project_stamps_dataset_owner_id(
        self, async_session: AsyncSession, fake_minio, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from api.services import datasets_service as ds_module

        monkeypatch.setattr(ds_module, "get_minio_client", lambda: fake_minio)

        project = await _make_project(async_session, owner_id=_OWNER)

        dataset, pdf_uri = await ds_module._persist_pdf_dataset(
            async_session,
            settings=get_settings(),
            project=project,
            task_type=TaskType.QA,
            name="seed-pdf",
            raw=b"%PDF-1.4 fake pdf bytes",
            num_pages=1,
            fd_report=_fd_report(),
        )

        assert dataset.owner_id == _OWNER
        assert pdf_uri is not None
        got = await async_session.get(Dataset, dataset.id)
        assert got is not None
        assert got.owner_id == _OWNER

    async def test_null_owner_project_yields_null_owner_dataset(
        self, async_session: AsyncSession, fake_minio, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from api.services import datasets_service as ds_module

        monkeypatch.setattr(ds_module, "get_minio_client", lambda: fake_minio)

        project = await _make_project(async_session, owner_id=None)

        dataset, _pdf_uri = await ds_module._persist_pdf_dataset(
            async_session,
            settings=get_settings(),
            project=project,
            task_type=TaskType.QA,
            name="seed-pdf",
            raw=b"%PDF-1.4 fake pdf bytes",
            num_pages=1,
            fd_report=_fd_report(),
        )

        assert dataset.owner_id is None


# =============================================================================
# 4. workers/tasks/data_generation.py — holdout child Dataset row
# =============================================================================


@pytest.fixture
def sync_sessionmaker():
    """In-memory sync sqlite engine + sessionmaker with the full ORM schema."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    yield maker
    engine.dispose()


def _install_worker_patches(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub):
    import workers.tasks.data_generation as dg_module

    @contextmanager
    def _fake_session_scope():
        session = sync_sessionmaker()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def _fake_redis_scope():
        yield fake_redis_pubsub.client

    monkeypatch.setattr(dg_module, "session_scope", _fake_session_scope)
    monkeypatch.setattr(dg_module, "sync_redis_scope", _fake_redis_scope)
    monkeypatch.setattr(dg_module, "get_minio_client", lambda: fake_minio)
    return dg_module


def _seed_project_and_dataset(
    sync_sessionmaker, *, project_id, dataset_id, owner_id, delete_project=False
):
    """Seed a Project + a PENDING parent Dataset (stamped with owner_id, as
    the real SDG-submit path does). Optionally delete the Project row
    afterwards (setting the dataset's project_id to NULL, mirroring W1-T1's
    ondelete=SET NULL) to prove the holdout child still inherits the
    parent's owner_id with no Project row left to read from at all.
    """
    session = sync_sessionmaker()
    try:
        project = Project(id=project_id, name="proj", task_type=TaskType.QA, owner_id=owner_id)
        session.add(project)
        dataset = Dataset(
            id=dataset_id,
            project_id=project_id,
            owner_id=owner_id,
            name="sdg-ds",
            task_type=TaskType.QA,
            source=DatasetSource.SDG,
            status=JobStatus.PENDING,
            num_samples=0,
        )
        session.add(dataset)
        session.commit()

        if delete_project:
            session.delete(session.get(Project, project_id))
            session.commit()
            # sqlite (unlike Postgres) doesn't enforce ON DELETE SET NULL by
            # default for an in-memory schema built via `create_all` without
            # PRAGMA foreign_keys=ON, so emulate W1-T1's ondelete=SET NULL
            # explicitly here to faithfully simulate the orphaned state.
            ds = session.get(Dataset, dataset_id)
            ds.project_id = None
            session.commit()
    finally:
        session.close()


def _build_payload(project_id, *, holdout_size=1) -> dict:
    request = SDGRequestDescriptionOnly(
        project_id=project_id,
        task_type=TaskType.QA,
        task_description="Answer questions about our 30-day return policy",
        num_samples=1,
        holdout_size=holdout_size,
    )
    return request.model_dump(mode="json")


def _fake_result(n=1):
    return SDGRunResult(
        valid_rows=[{"question": f"q{i}", "answer": f"a{i}"} for i in range(n)],
        rejected_count=0,
        duplicate_count=0,
        judge_rejected_count=0,
        judge_parse_failures=0,
        api_calls=1,
    )


def _holdout_dataset(sync_sessionmaker, parent_id):
    session = sync_sessionmaker()
    try:
        return session.execute(
            select(Dataset).where(Dataset.parent_dataset_id == parent_id)
        ).scalar_one()
    finally:
        session.close()


class TestHoldoutChildInheritsParentOwnerId:
    def test_holdout_owner_id_matches_parent_no_project_row_loaded(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )

        async def _fake_run_generator(**kwargs):
            kwargs["usage"].add(dg_module.sdg_models.GENERATOR, STAGE_GENERATE, 1000, 500)
            return _fake_result(4)

        monkeypatch.setattr(dg_module, "_run_generator", _fake_run_generator)

        project_id, dataset_id = uuid4(), uuid4()
        _seed_project_and_dataset(
            sync_sessionmaker, project_id=project_id, dataset_id=dataset_id, owner_id=_OWNER
        )

        result = dg_module.generate_synthetic_data.apply(
            kwargs={
                "request_payload": _build_payload(project_id, holdout_size=1),
                "dataset_id": str(dataset_id),
            }
        )
        assert result.successful(), f"task raised: {result.result!r}"

        holdout = _holdout_dataset(sync_sessionmaker, dataset_id)
        assert holdout.owner_id == _OWNER

    def test_holdout_owner_id_null_when_parent_owner_id_null(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )

        async def _fake_run_generator(**kwargs):
            kwargs["usage"].add(dg_module.sdg_models.GENERATOR, STAGE_GENERATE, 1000, 500)
            return _fake_result(4)

        monkeypatch.setattr(dg_module, "_run_generator", _fake_run_generator)

        project_id, dataset_id = uuid4(), uuid4()
        _seed_project_and_dataset(
            sync_sessionmaker, project_id=project_id, dataset_id=dataset_id, owner_id=None
        )

        result = dg_module.generate_synthetic_data.apply(
            kwargs={
                "request_payload": _build_payload(project_id, holdout_size=1),
                "dataset_id": str(dataset_id),
            }
        )
        assert result.successful(), f"task raised: {result.result!r}"

        holdout = _holdout_dataset(sync_sessionmaker, dataset_id)
        assert holdout.owner_id is None

    def test_holdout_owner_id_survives_owning_project_deletion(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        """The parent Dataset row was orphaned (project_id set to NULL,
        Project row gone) before the SDG task ever runs against it. Even
        with no Project row reachable at all, the holdout child must still
        inherit `owner_id` from the parent Dataset row, not from a
        (nonexistent) Project lookup.
        """
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )

        async def _fake_run_generator(**kwargs):
            kwargs["usage"].add(dg_module.sdg_models.GENERATOR, STAGE_GENERATE, 1000, 500)
            return _fake_result(4)

        monkeypatch.setattr(dg_module, "_run_generator", _fake_run_generator)

        project_id, dataset_id = uuid4(), uuid4()
        _seed_project_and_dataset(
            sync_sessionmaker,
            project_id=project_id,
            dataset_id=dataset_id,
            owner_id=_OWNER,
            delete_project=True,
        )

        # Sanity: the parent dataset really is orphaned (no Project row) but
        # still carries the owner_id before the task ever touches it.
        session = sync_sessionmaker()
        try:
            parent = session.get(Dataset, dataset_id)
            assert parent.project_id is None
            assert parent.owner_id == _OWNER
            assert session.get(Project, project_id) is None
        finally:
            session.close()

        result = dg_module.generate_synthetic_data.apply(
            kwargs={
                "request_payload": _build_payload(project_id, holdout_size=1),
                "dataset_id": str(dataset_id),
            }
        )
        assert result.successful(), f"task raised: {result.result!r}"

        holdout = _holdout_dataset(sync_sessionmaker, dataset_id)
        assert holdout.owner_id == _OWNER
