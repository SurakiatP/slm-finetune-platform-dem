"""Unit tests proving the Dataset.status / error_message lifecycle end-to-end.

Layers covered (see class docstrings):
  1. Model-level    — column exists, nullability, Python-side default.
  2. Schema-level    — DatasetResponse validation (required `status`, optional
     `error_message`).
  3. Async service-level — `sdg_service.submit_sdg_job` (PENDING placeholder)
     and `datasets_service._persist_jsonl_dataset` (COMPLETED seed upload),
     against an in-memory aiosqlite engine.
  4. Worker-level    — the `generate_synthetic_data` Celery task, run
     synchronously via `.apply(...)` against an in-memory sync sqlite engine,
     covering both the success path (PENDING -> RUNNING -> COMPLETED) and the
     failure path (PENDING -> RUNNING -> FAILED + error_message set).

Everything here runs against in-memory fakes only — no Postgres, no Docker,
no GPU, no real OpenRouter/Celery-broker calls.
"""

from __future__ import annotations

from contextlib import contextmanager
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import sessionmaker

from api.models.base import Base
from api.models.dataset import Dataset
from api.models.project import Project
from api.schemas.datasets import DatasetResponse
from api.schemas.enums import DatasetSource, JobStatus, TaskType
from api.schemas.sdg import SDGRequestDescriptionOnly
from api.schemas.upload import FormatDetectionReport
from ai_engine.data_gen.generator import SDGRunResult


# ---- KNOWN GOTCHA shim: JSONB is Postgres-only, sqlite needs a stand-in ----


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


# =============================================================================
# 1. Model-level (no DB needed)
# =============================================================================


class TestDatasetModelColumns:
    def test_status_column_exists_not_nullable_default_pending(self) -> None:
        col = Dataset.__table__.columns["status"]
        assert col is not None
        assert col.nullable is False
        # Python-side default (not server_default) resolves to JobStatus.PENDING.
        assert col.default is not None
        assert col.default.arg == JobStatus.PENDING

    def test_error_message_column_exists_and_is_nullable(self) -> None:
        col = Dataset.__table__.columns["error_message"]
        assert col is not None
        assert col.nullable is True
        assert col.type.length == 4000


# =============================================================================
# 2. Schema-level (no DB needed)
# =============================================================================


def _base_response_kwargs() -> dict:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return dict(
        id=uuid4(),
        project_id=uuid4(),
        parent_dataset_id=None,
        name="ds",
        task_type=TaskType.QA,
        source=DatasetSource.SDG,
        num_samples=0,
        storage_uri=None,
        size_bytes=None,
        generation_metadata=None,
        created_at=now,
        updated_at=now,
    )


class TestDatasetResponseSchema:
    def test_completed_round_trips(self) -> None:
        payload = {**_base_response_kwargs(), "status": "completed", "error_message": None}
        resp = DatasetResponse.model_validate(payload)
        assert resp.status is JobStatus.COMPLETED
        assert resp.error_message is None

    def test_failed_round_trips_with_error_message(self) -> None:
        payload = {**_base_response_kwargs(), "status": "failed", "error_message": "boom"}
        resp = DatasetResponse.model_validate(payload)
        assert resp.status is JobStatus.FAILED
        assert resp.error_message == "boom"

    def test_missing_status_is_required_field_error(self) -> None:
        payload = _base_response_kwargs()  # no "status" key at all
        with pytest.raises(ValidationError) as excinfo:
            DatasetResponse.model_validate(payload)
        errors = excinfo.value.errors()
        assert any(e["loc"] == ("status",) and e["type"] == "missing" for e in errors)


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


class TestSubmitSdgJobAsync:
    async def test_submit_sdg_job_creates_pending_dataset(
        self, async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from api.services import sdg_service

        project = Project(id=uuid4(), name="proj", task_type=TaskType.QA)
        async_session.add(project)
        await async_session.flush()

        # Patch the exact use-site: sdg_service does a local
        # `from workers.tasks.data_generation import generate_synthetic_data`
        # and calls `.apply_async(...)` on it — patch that task object's
        # bound method directly so no Celery broker/Redis is touched.
        import workers.tasks.data_generation as dg_module

        fake_async_result = type("FakeAsyncResult", (), {"id": "fake-job-id"})()

        def _fake_apply_async(**kwargs):
            return fake_async_result

        monkeypatch.setattr(
            dg_module.generate_synthetic_data, "apply_async", _fake_apply_async
        )

        request = SDGRequestDescriptionOnly(
            project_id=project.id,
            task_type=TaskType.QA,
            task_description="Answer questions about our return policy",
            num_samples=10,
            holdout_size=0,
        )

        response = await sdg_service.submit_sdg_job(async_session, request)

        assert response.job_id == "fake-job-id"
        assert response.status is JobStatus.PENDING

        got = await async_session.get(Dataset, response.dataset_id)
        assert got is not None
        assert got.status == JobStatus.PENDING
        assert got.source == DatasetSource.SDG


class TestPersistJsonlDatasetAsync:
    async def test_persist_jsonl_dataset_is_completed(
        self, async_session: AsyncSession, fake_minio, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from api.services import datasets_service as ds_module
        from api.core.config import get_settings

        # `fake_minio` fixture patches `workers.storage.get_minio_client`, but
        # `datasets_service.py` did `from workers.storage import
        # get_minio_client`, binding its own module-local name at import
        # time — patching the source module's attribute afterwards doesn't
        # reach that alias. Patch the use-site directly (mirrors the
        # `session_scope` gotcha called out for the worker-level tests).
        monkeypatch.setattr(ds_module, "get_minio_client", lambda: fake_minio)

        project = Project(id=uuid4(), name="proj", task_type=TaskType.QA)
        async_session.add(project)
        await async_session.flush()

        valid_rows = [{"question": "q1", "answer": "a1"}]
        fd_report = FormatDetectionReport(
            ran=False,
            model_used=None,
            field_mapping={},
            rows_total=1,
            rows_canonicalised=1,
            rows_dropped=0,
            notes="already canonical",
        )

        dataset = await ds_module._persist_jsonl_dataset(
            async_session,
            settings=get_settings(),
            project=project,
            task_type=TaskType.QA,
            name="seed-ds",
            valid_rows=valid_rows,
            fd_report=fd_report,
        )

        assert dataset.status == JobStatus.COMPLETED
        assert dataset.source == DatasetSource.SEED
        assert dataset.storage_uri is not None
        assert dataset.num_samples == 1

        # Re-fetch to make sure it was actually persisted, not just mutated
        # in-memory.
        got = await async_session.get(Dataset, dataset.id)
        assert got is not None
        assert got.status == JobStatus.COMPLETED


# =============================================================================
# 4. Worker-level (Celery task, sync sqlite)
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
    """Shared monkeypatching for the `generate_synthetic_data` task tests.

    Patches (at the *use-site* inside `workers.tasks.data_generation`, per
    the module's own import style — `from X import Y` binds a local alias
    that patching `X.Y` afterwards does not reach):
      - `session_scope`     -> wraps a session from the sync sqlite engine,
        mirroring the real session_scope's commit/rollback/close semantics.
      - `sync_redis_scope`  -> yields the fakeredis client from
        `fake_redis_pubsub` instead of opening a real Redis connection.
      - `get_minio_client`  -> returns the `fake_minio` in-memory stub.
    """
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


def _seed_project_and_dataset(sync_sessionmaker, *, project_id, dataset_id):
    session = sync_sessionmaker()
    try:
        project = Project(id=project_id, name="proj", task_type=TaskType.QA)
        session.add(project)
        dataset = Dataset(
            id=dataset_id,
            project_id=project_id,
            name="sdg-ds",
            task_type=TaskType.QA,
            source=DatasetSource.SDG,
            status=JobStatus.PENDING,
            num_samples=0,
        )
        session.add(dataset)
        session.commit()
    finally:
        session.close()


def _build_description_only_payload(project_id) -> dict:
    request = SDGRequestDescriptionOnly(
        project_id=project_id,
        task_type=TaskType.QA,
        task_description="Answer questions about our 30-day return policy",
        num_samples=1,
        holdout_size=0,
    )
    return request.model_dump(mode="json")


class TestGenerateSyntheticDataTaskSuccess:
    def test_success_path_flips_pending_running_completed(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )

        async def _fake_run_generator(**kwargs):
            return SDGRunResult(
                valid_rows=[{"question": "q", "answer": "a"}],
                rejected_count=0,
                duplicate_count=0,
                judge_rejected_count=0,
                judge_parse_failures=0,
                api_calls=1,
            )

        monkeypatch.setattr(dg_module, "_run_generator", _fake_run_generator)

        project_id = uuid4()
        dataset_id = uuid4()
        _seed_project_and_dataset(sync_sessionmaker, project_id=project_id, dataset_id=dataset_id)

        payload = _build_description_only_payload(project_id)

        result = dg_module.generate_synthetic_data.apply(
            kwargs={"request_payload": payload, "dataset_id": str(dataset_id)}
        )

        assert result.successful(), f"task raised: {result.result!r}"

        session = sync_sessionmaker()
        try:
            ds = session.get(Dataset, dataset_id)
            assert ds is not None
            assert ds.status == JobStatus.COMPLETED
            assert ds.storage_uri is not None
            assert ds.num_samples == 1
            assert ds.error_message is None
        finally:
            session.close()


class TestGenerateSyntheticDataTaskFailure:
    def test_failure_path_flips_pending_running_failed(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )

        async def _raising_run_generator(**kwargs):
            raise RuntimeError("injected SDG failure for test")

        monkeypatch.setattr(dg_module, "_run_generator", _raising_run_generator)

        project_id = uuid4()
        dataset_id = uuid4()
        _seed_project_and_dataset(sync_sessionmaker, project_id=project_id, dataset_id=dataset_id)

        payload = _build_description_only_payload(project_id)

        result = dg_module.generate_synthetic_data.apply(
            kwargs={"request_payload": payload, "dataset_id": str(dataset_id)}
        )

        # Celery's eager `.apply()` captures the exception into the
        # EagerResult rather than letting it propagate out of this call.
        assert not result.successful()
        assert result.failed()
        assert isinstance(result.result, RuntimeError)

        session = sync_sessionmaker()
        try:
            ds = session.get(Dataset, dataset_id)
            assert ds is not None
            assert ds.status == JobStatus.FAILED
            assert ds.error_message
            assert "injected SDG failure for test" in ds.error_message
        finally:
            session.close()
