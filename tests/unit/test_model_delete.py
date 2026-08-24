"""Unit tests for `DELETE /api/v1/models/{id}`.

Covers `api.services.model_service.purge_artifact` (the public, reusable
best-effort-cleanup helper) and `delete_model` (ownership -> in-flight-export
guard -> purge -> audit -> commit), plus the route wiring in
`api.routers.models`.

Runs entirely against an in-memory aiosqlite engine with fake MinIO/Ollama
doubles — no Postgres, no Docker, no real network calls.

sqlite gotchas this file works around (both documented precedents elsewhere
in this suite):
  • `JSONB` (postgres-only) needs a `@compiles` shim to run against sqlite —
    see `tests/unit/test_dataset_status.py`.
  • FK `ondelete` actions (here: `EvaluationRun.model_artifact_id`'s
    `ondelete="CASCADE"`) only fire under `PRAGMA foreign_keys=ON`, which
    sqlite defaults OFF — see
    `tests/unit/test_wave1_hardening_dataset_project_delete_cascade.py`.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from api.core import request_context
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.evaluation_run import EvaluationRun
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.enums import DatasetSource, JobStatus, TaskType, TrainingMode
from api.services import model_service


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


# ---- fixtures ---------------------------------------------------------------


@pytest.fixture
async def db():
    """In-memory aiosqlite engine, full ORM schema, FK enforcement ON.

    FK enforcement matters here specifically for the cascade test below —
    without `PRAGMA foreign_keys=ON`, sqlite silently ignores `ondelete=...`
    entirely and the cascade would appear to "not happen" for the wrong
    reason (no enforcement) rather than the right one (the FK really is/
    isn't CASCADE).
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_fks(dbapi_conn, _record):  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def _clear_request_context():
    yield
    request_context.clear()


class _FakeMinio:
    """Minimal MinIO double covering only what `remove_prefix` calls:
    `list_objects` + `remove_object`. Records every removed (bucket, key)
    pair so tests can assert on exactly what got purged.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], bytes] = {}
        self.removed: list[tuple[str, str]] = []

    def seed(self, bucket: str, key: str) -> None:
        self._store[(bucket, key)] = b"x"

    def list_objects(self, bucket_name: str, prefix: str = "", recursive: bool = False):
        for bucket, key in list(self._store):
            if bucket != bucket_name:
                continue
            if prefix and not key.startswith(prefix):
                continue
            yield SimpleNamespace(object_name=key)

    def remove_object(self, bucket_name: str, object_name: str) -> None:
        self._store.pop((bucket_name, object_name), None)
        self.removed.append((bucket_name, object_name))


@pytest.fixture
def fake_minio(monkeypatch: pytest.MonkeyPatch) -> _FakeMinio:
    """Patches the use-site (`model_service.get_minio_client`), not
    `workers.storage.get_minio_client` — `model_service.py` did
    `from workers.storage import get_minio_client`, binding its own
    module-local alias at import time, so patching the source module's
    attribute afterwards would not reach it (same gotcha called out in
    `tests/conftest.py`'s `fake_minio` fixture docstring).
    """
    client = _FakeMinio()
    monkeypatch.setattr(model_service, "get_minio_client", lambda: client)
    return client


@pytest.fixture
def fake_ollama(monkeypatch: pytest.MonkeyPatch):
    """Patches `model_service.OllamaClient` with a double that records every
    `delete_model(tag)` call and can be told to raise, to prove the
    "tolerate already-gone/unreachable" swallow-and-log behavior.
    """
    state = SimpleNamespace(calls=[], raise_error=False)

    class _FakeOllamaClient:
        def __init__(self, base_url: str) -> None:
            self.base_url = base_url

        def delete_model(self, tag: str) -> None:
            state.calls.append(tag)
            if state.raise_error:
                raise RuntimeError("ollama unreachable (injected for test)")

    monkeypatch.setattr(model_service, "OllamaClient", _FakeOllamaClient)
    return state


# ---- seed helpers -------------------------------------------------------


async def _seed_project_job_dataset(db: AsyncSession):
    project = Project(id=uuid4(), name="proj", task_type=TaskType.QA)
    db.add(project)
    await db.flush()

    dataset = Dataset(
        id=uuid4(),
        project_id=project.id,
        name="ds",
        task_type=TaskType.QA,
        source=DatasetSource.SEED,
        status=JobStatus.COMPLETED,
        num_samples=1,
        storage_uri="s3://datasets/ds.jsonl",
    )
    db.add(dataset)
    await db.flush()

    job = TrainingJob(
        id=uuid4(),
        project_id=project.id,
        dataset_id=dataset.id,
        mode=TrainingMode.MANUAL,
        status=JobStatus.COMPLETED,
        base_model="base-model",
        config_json={},
    )
    db.add(job)
    await db.flush()
    return project, dataset, job


async def _seed_artifact(db: AsyncSession, job: TrainingJob, **overrides) -> ModelArtifact:
    defaults = dict(
        id=uuid4(),
        training_job_id=job.id,
        name="artifact",
        base_model="base-model",
        gguf_uri="s3://models/a/gguf",
        safetensors_uri="s3://models/a/safetensors",
        lora_adapter_uri="s3://models/a/lora",
        ollama_model_tag="artifact:latest",
        export_status=None,
    )
    defaults.update(overrides)
    artifact = ModelArtifact(**defaults)
    db.add(artifact)
    await db.flush()
    return artifact


# ---- happy path ---------------------------------------------------------


class TestDeleteModelHappyPath:
    async def test_delete_removes_row_purges_storage_and_ollama(
        self, db: AsyncSession, fake_minio: _FakeMinio, fake_ollama
    ) -> None:
        _, _, job = await _seed_project_job_dataset(db)
        fake_minio.seed("models", "a/gguf/model.gguf")
        fake_minio.seed("models", "a/safetensors/model.safetensors")
        fake_minio.seed("models", "a/lora/adapter_model.bin")
        artifact = await _seed_artifact(db, job)
        artifact_id = artifact.id

        await model_service.delete_model(db, artifact_id, user=None)

        assert await db.get(ModelArtifact, artifact_id) is None

        removed = set(fake_minio.removed)
        assert ("models", "a/gguf/model.gguf") in removed
        assert ("models", "a/safetensors/model.safetensors") in removed
        assert ("models", "a/lora/adapter_model.bin") in removed
        assert fake_ollama.calls == ["artifact:latest"]

    async def test_delete_skips_null_uris_and_null_ollama_tag(
        self, db: AsyncSession, fake_minio: _FakeMinio, fake_ollama
    ) -> None:
        """An artifact whose training never got far enough to export
        anything (all URIs null, no ollama tag) must still delete cleanly —
        `purge_artifact` should skip storage/ollama work entirely rather
        than erroring on a `None` URI."""
        _, _, job = await _seed_project_job_dataset(db)
        artifact = await _seed_artifact(
            db,
            job,
            gguf_uri=None,
            safetensors_uri=None,
            lora_adapter_uri=None,
            ollama_model_tag=None,
        )
        artifact_id = artifact.id

        await model_service.delete_model(db, artifact_id, user=None)

        assert await db.get(ModelArtifact, artifact_id) is None
        assert fake_minio.removed == []
        assert fake_ollama.calls == []


# ---- in-flight export guard ----------------------------------------------


class TestDeleteModelExportInFlight:
    @pytest.mark.parametrize("in_flight_status", [JobStatus.PENDING, JobStatus.RUNNING])
    async def test_409_while_export_pending_or_running_and_row_survives(
        self,
        db: AsyncSession,
        fake_minio: _FakeMinio,
        fake_ollama,
        in_flight_status: JobStatus,
    ) -> None:
        _, _, job = await _seed_project_job_dataset(db)
        artifact = await _seed_artifact(
            db,
            job,
            export_status=in_flight_status,
            export_celery_task_id="celery-task-123",
        )
        artifact_id = artifact.id

        with pytest.raises(HTTPException) as excinfo:
            await model_service.delete_model(db, artifact_id, user=None)

        assert excinfo.value.status_code == 409
        assert "celery-task-123" in excinfo.value.detail
        assert "export/cancel" in excinfo.value.detail

        db.expire_all()
        assert await db.get(ModelArtifact, artifact_id) is not None
        assert fake_minio.removed == []
        assert fake_ollama.calls == []


class TestDeleteModelExportNotBlocking:
    @pytest.mark.parametrize(
        "terminal_or_never_run",
        [None, JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED],
    )
    async def test_204_when_export_terminal_or_never_run(
        self,
        db: AsyncSession,
        fake_minio: _FakeMinio,
        fake_ollama,
        terminal_or_never_run: JobStatus | None,
    ) -> None:
        _, _, job = await _seed_project_job_dataset(db)
        artifact = await _seed_artifact(db, job, export_status=terminal_or_never_run)
        artifact_id = artifact.id

        await model_service.delete_model(db, artifact_id, user=None)

        assert await db.get(ModelArtifact, artifact_id) is None


# ---- best-effort swallow behavior -----------------------------------------


class TestDeleteModelExternalFailuresAreSwallowed:
    async def test_ollama_raising_is_swallowed_and_row_still_deletes(
        self, db: AsyncSession, fake_minio: _FakeMinio, fake_ollama
    ) -> None:
        fake_ollama.raise_error = True
        _, _, job = await _seed_project_job_dataset(db)
        artifact = await _seed_artifact(db, job)
        artifact_id = artifact.id

        await model_service.delete_model(db, artifact_id, user=None)

        assert await db.get(ModelArtifact, artifact_id) is None
        assert fake_ollama.calls == ["artifact:latest"]

    async def test_minio_raising_is_swallowed_and_row_still_deletes(
        self, db: AsyncSession, fake_ollama, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _RaisingMinio:
            def list_objects(self, *args, **kwargs):
                raise RuntimeError("minio unreachable (injected for test)")

        monkeypatch.setattr(model_service, "get_minio_client", lambda: _RaisingMinio())

        _, _, job = await _seed_project_job_dataset(db)
        artifact = await _seed_artifact(db, job)
        artifact_id = artifact.id

        await model_service.delete_model(db, artifact_id, user=None)

        assert await db.get(ModelArtifact, artifact_id) is None
        assert fake_ollama.calls == ["artifact:latest"]


# ---- EvaluationRun FK cascade ----------------------------------------------


class TestEvaluationRunCascade:
    def test_fk_is_declared_ondelete_cascade(self) -> None:
        """Sanity-check the premise before relying on it below: this is
        `EvaluationRun.model_artifact_id`'s actual FK metadata, not an
        assumption."""
        col = EvaluationRun.__table__.columns["model_artifact_id"]
        fks = list(col.foreign_keys)
        assert len(fks) == 1
        assert fks[0].ondelete == "CASCADE"

    async def test_evaluation_run_is_cascade_removed_with_the_artifact(
        self, db: AsyncSession, fake_minio: _FakeMinio, fake_ollama
    ) -> None:
        """The FK says CASCADE, but `model_artifact_id` is also NOT NULL and
        `ModelArtifact.evaluation_runs` has no `passive_deletes` — so a bare
        `await db.delete(artifact)` would make SQLAlchemy try to NULL that
        column out first and fail with an IntegrityError on every backend,
        not just sqlite. `purge_artifact` sidesteps this with an explicit
        Core-level delete of the dependent rows before the ORM delete (see
        its docstring) — this test proves the end result still matches the
        FK's stated intent: evaluations don't outlive their model."""
        _, dataset, job = await _seed_project_job_dataset(db)
        artifact = await _seed_artifact(db, job)
        eval_run = EvaluationRun(
            id=uuid4(),
            model_artifact_id=artifact.id,
            dataset_id=dataset.id,
            status=JobStatus.COMPLETED,
        )
        db.add(eval_run)
        await db.flush()
        eval_run_id = eval_run.id
        artifact_id = artifact.id

        await model_service.delete_model(db, artifact_id, user=None)

        db.expire_all()
        assert await db.get(ModelArtifact, artifact_id) is None
        assert await db.get(EvaluationRun, eval_run_id) is None


# ---- route wiring -----------------------------------------------------------


class TestRouteWiring:
    def test_delete_route_is_registered_in_the_openapi_spec(self) -> None:
        from api.main import app

        spec = app.openapi()
        assert "delete" in spec["paths"]["/api/v1/models/{model_id}"]
