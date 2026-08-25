"""Unit tests for `DELETE /api/v1/trainings/{id}` (task D9).

`DELETE` used to be a cancel alias (`trainings_service.cancel_training`).
It is now repurposed into a hard-delete: `trainings_service.delete_training`.
Covers:
  - ownership -> terminal-status guard -> artifact purge -> job delete ->
    audit -> commit, mirroring `api.services.model_service.delete_model`'s
    shape (see `tests/unit/test_model_delete.py`).
  - `POST /{id}/cancel` (`trainings_service.cancel_training`) is untouched
    by this change — a smoke test below pins that.

Runs entirely against an in-memory aiosqlite engine with fake MinIO/Ollama
doubles — no Postgres, no Docker, no real network calls.

sqlite gotcha this file works around (documented precedent):
  • `JSONB` (postgres-only) needs a `@compiles` shim to run against sqlite —
    see `tests/unit/test_dataset_status.py`.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
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
from api.services import model_service, trainings_service


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


# ---- fixtures ---------------------------------------------------------------


@pytest.fixture
async def db():
    """In-memory aiosqlite engine, full ORM schema."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
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
    """Minimal MinIO double covering only what `remove_prefix` calls."""

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
    """Patches the use-site (`model_service.get_minio_client`) — same
    reasoning as `tests/unit/test_model_delete.py`'s fixture of the same
    name: `purge_artifact` lives in `model_service.py`, which bound its own
    module-local alias at import time, so it's that module's attribute that
    must be patched, not `workers.storage`'s.
    """
    client = _FakeMinio()
    monkeypatch.setattr(model_service, "get_minio_client", lambda: client)
    return client


@pytest.fixture
def fake_ollama(monkeypatch: pytest.MonkeyPatch):
    """Patches `model_service.OllamaClient` with a double that records every
    `delete_model(tag)` call.
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


@pytest.fixture
def spy_revoke(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    """Capture task ids passed to `revoke_celery_task` via `trainings_service`
    — used to assert `delete_training`'s 409 path never reaches for the
    Celery broker (only `cancel_training` does).
    """
    calls: list[str | None] = []

    def _spy(task_id: str | None, *, context: str) -> None:
        calls.append(task_id)

    monkeypatch.setattr(trainings_service, "revoke_celery_task", _spy)
    return calls


# ---- seed helpers -------------------------------------------------------


async def _seed_project_and_dataset(db: AsyncSession):
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
    return project, dataset


async def _seed_job(db: AsyncSession, project: Project, dataset: Dataset, **overrides) -> TrainingJob:
    defaults = dict(
        id=uuid4(),
        project_id=project.id,
        dataset_id=dataset.id,
        mode=TrainingMode.MANUAL,
        status=JobStatus.COMPLETED,
        base_model="base-model",
        config_json={},
        celery_task_id="train-task",
    )
    defaults.update(overrides)
    job = TrainingJob(**defaults)
    db.add(job)
    await db.flush()
    return job


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


# ---- happy path: terminal statuses -----------------------------------------


class TestDeleteTrainingHappyPath:
    @pytest.mark.parametrize(
        "terminal_status", [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]
    )
    async def test_204_deletes_job_and_purges_artifact(
        self,
        db: AsyncSession,
        fake_minio: _FakeMinio,
        fake_ollama,
        terminal_status: JobStatus,
    ) -> None:
        project, dataset = await _seed_project_and_dataset(db)
        job = await _seed_job(db, project, dataset, status=terminal_status)
        fake_minio.seed("models", "a/gguf/model.gguf")
        fake_minio.seed("models", "a/safetensors/model.safetensors")
        fake_minio.seed("models", "a/lora/adapter_model.bin")
        artifact = await _seed_artifact(db, job)
        job_id = job.id
        artifact_id = artifact.id

        await trainings_service.delete_training(db, job_id, user=None)

        assert await db.get(TrainingJob, job_id) is None
        assert await db.get(ModelArtifact, artifact_id) is None

        removed = set(fake_minio.removed)
        assert ("models", "a/gguf/model.gguf") in removed
        assert ("models", "a/safetensors/model.safetensors") in removed
        assert ("models", "a/lora/adapter_model.bin") in removed
        assert fake_ollama.calls == ["artifact:latest"]

    async def test_204_evaluation_runs_are_purged_with_the_artifact(
        self, db: AsyncSession, fake_minio: _FakeMinio, fake_ollama
    ) -> None:
        project, dataset = await _seed_project_and_dataset(db)
        job = await _seed_job(db, project, dataset, status=JobStatus.COMPLETED)
        artifact = await _seed_artifact(db, job)
        eval_run = EvaluationRun(
            id=uuid4(),
            model_artifact_id=artifact.id,
            dataset_id=dataset.id,
            status=JobStatus.COMPLETED,
        )
        db.add(eval_run)
        await db.flush()
        job_id = job.id
        eval_run_id = eval_run.id

        await trainings_service.delete_training(db, job_id, user=None)

        db.expire_all()
        assert await db.get(TrainingJob, job_id) is None
        assert await db.get(EvaluationRun, eval_run_id) is None


class TestDeleteTrainingNoArtifact:
    async def test_204_when_no_artifact_ever_created(
        self, db: AsyncSession, fake_minio: _FakeMinio, fake_ollama
    ) -> None:
        """A training that failed before producing a `ModelArtifact` (or one
        that was never exported far enough) must still delete cleanly, with
        the artifact-purge step skipped entirely."""
        project, dataset = await _seed_project_and_dataset(db)
        job = await _seed_job(db, project, dataset, status=JobStatus.FAILED)
        job_id = job.id

        await trainings_service.delete_training(db, job_id, user=None)

        assert await db.get(TrainingJob, job_id) is None
        assert fake_minio.removed == []
        assert fake_ollama.calls == []


# ---- in-flight guard --------------------------------------------------------


class TestDeleteTrainingInFlight:
    @pytest.mark.parametrize("in_flight_status", [JobStatus.PENDING, JobStatus.RUNNING])
    async def test_409_while_pending_or_running_rows_survive_no_side_effects(
        self,
        db: AsyncSession,
        fake_minio: _FakeMinio,
        fake_ollama,
        spy_revoke: list[str | None],
        in_flight_status: JobStatus,
    ) -> None:
        project, dataset = await _seed_project_and_dataset(db)
        job = await _seed_job(db, project, dataset, status=in_flight_status)
        artifact = await _seed_artifact(db, job)
        job_id = job.id
        artifact_id = artifact.id

        with pytest.raises(HTTPException) as excinfo:
            await trainings_service.delete_training(db, job_id, user=None)

        assert excinfo.value.status_code == 409
        assert str(job_id) in excinfo.value.detail
        assert in_flight_status.value in excinfo.value.detail
        assert f"/api/v1/trainings/{job_id}/cancel" in excinfo.value.detail

        db.expire_all()
        assert await db.get(TrainingJob, job_id) is not None
        assert await db.get(ModelArtifact, artifact_id) is not None
        assert fake_minio.removed == []
        assert fake_ollama.calls == []
        assert spy_revoke == [], "delete must never touch the Celery broker"


# ---- POST cancel is unaffected ----------------------------------------------


class TestCancelTrainingUnaffectedByDelete:
    async def test_post_cancel_still_flips_and_revokes(
        self, db: AsyncSession, spy_revoke: list[str | None]
    ) -> None:
        project, dataset = await _seed_project_and_dataset(db)
        job = await _seed_job(db, project, dataset, status=JobStatus.RUNNING)
        job_id = job.id

        result = await trainings_service.cancel_training(db, job_id, user=None)

        assert result == {"training_id": str(job_id), "status": "cancelled"}
        assert spy_revoke == ["train-task"]

        db.expire_all()
        refreshed = await db.get(TrainingJob, job_id)
        assert refreshed is not None
        assert refreshed.status is JobStatus.CANCELLED

    @pytest.mark.parametrize(
        "terminal_status", [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]
    )
    async def test_post_cancel_idempotent_on_terminal(
        self, db: AsyncSession, spy_revoke: list[str | None], terminal_status: JobStatus
    ) -> None:
        project, dataset = await _seed_project_and_dataset(db)
        job = await _seed_job(db, project, dataset, status=terminal_status)
        job_id = job.id

        result = await trainings_service.cancel_training(db, job_id, user=None)

        assert result == {"training_id": str(job_id), "status": terminal_status.value}
        assert spy_revoke == []


# ---- route wiring -----------------------------------------------------------


class TestRouteWiring:
    def test_delete_route_returns_204_in_the_openapi_spec(self) -> None:
        from api.main import app

        spec = app.openapi()
        delete_op = spec["paths"]["/api/v1/trainings/{training_id}"]["delete"]
        assert "204" in delete_op["responses"]

    def test_cancel_post_route_still_exists(self) -> None:
        from api.main import app

        spec = app.openapi()
        assert "post" in spec["paths"]["/api/v1/trainings/{training_id}/cancel"]
