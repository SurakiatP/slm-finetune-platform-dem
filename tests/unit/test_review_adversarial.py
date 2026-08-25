"""Adversarial review tests for the D8/D9/D10/D11 delete/decouple/rename work.

Written by the final reviewer, deliberately aimed at seams the per-task
agents' own suites do NOT cover:

  1. `DELETE /trainings/{id}` on a *terminal* run whose `ModelArtifact` still
     has an **export in flight** — `delete_model` 409s on that state, but
     `delete_training` reaches the same artifact through `purge_artifact`
     via a different guard (job status, not export status). If the two
     disagree, the export guard is trivially bypassable by deleting the
     parent training instead of the model.
  2. Deleting the same model twice (idempotency / stale-UI double-click).
  3. Renaming a dataset to the name it already has (no-op rename).
  4. An orphaned training run (`project_id IS NULL`, migration
     `0012_training_decouple`) as seen by `list_trainings` with auth OFF —
     the playground/join path — and by `assert_training_access` with auth ON.
  5. `delete_training`'s audit row: `audit_service.record(...)` is called
     AFTER `await db.delete(job)` and reads attributes off the
     just-deleted instance, so pin that the row really lands with the right
     `resource_id`/`project_id` rather than silently vanishing or blowing up
     at flush time.

Same in-memory aiosqlite + fake MinIO/Ollama harness as
`tests/unit/test_model_delete.py` / `test_training_delete.py`.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from api.core import request_context
from api.core.auth import CurrentUser
from api.models.audit_event import AuditEvent
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.datasets import DatasetUpdate
from api.schemas.enums import DatasetSource, JobStatus, TaskType, TrainingMode
from api.services import datasets_service, model_service, ownership, trainings_service


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


# ---- fixtures ---------------------------------------------------------------


@pytest.fixture
async def db():
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
    def __init__(self) -> None:
        self.removed: list[tuple[str, str]] = []

    def list_objects(self, bucket_name: str, prefix: str = "", recursive: bool = False):
        return iter(())

    def remove_object(self, bucket_name: str, object_name: str) -> None:
        self.removed.append((bucket_name, object_name))


@pytest.fixture
def fake_minio(monkeypatch: pytest.MonkeyPatch) -> _FakeMinio:
    client = _FakeMinio()
    monkeypatch.setattr(model_service, "get_minio_client", lambda: client)
    return client


@pytest.fixture
def fake_ollama(monkeypatch: pytest.MonkeyPatch):
    state = SimpleNamespace(calls=[])

    class _FakeOllamaClient:
        def __init__(self, base_url: str) -> None:
            self.base_url = base_url

        def delete_model(self, tag: str) -> None:
            state.calls.append(tag)

    monkeypatch.setattr(model_service, "OllamaClient", _FakeOllamaClient)
    return state


# ---- seed helpers -----------------------------------------------------------


async def _seed(db: AsyncSession, *, owner_id: str | None = None):
    project = Project(id=uuid4(), name="proj", task_type=TaskType.QA, owner_id=owner_id)
    db.add(project)
    await db.flush()
    dataset = Dataset(
        id=uuid4(),
        project_id=project.id,
        owner_id=owner_id,
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


async def _seed_job(db: AsyncSession, project: Project | None, dataset: Dataset, **over) -> TrainingJob:
    defaults = dict(
        id=uuid4(),
        project_id=project.id if project is not None else None,
        dataset_id=dataset.id,
        mode=TrainingMode.MANUAL,
        status=JobStatus.COMPLETED,
        base_model="base-model",
        config_json={},
        celery_task_id="train-task",
    )
    defaults.update(over)
    job = TrainingJob(**defaults)
    db.add(job)
    await db.flush()
    return job


async def _seed_artifact(db: AsyncSession, job: TrainingJob, **over) -> ModelArtifact:
    defaults = dict(
        id=uuid4(),
        training_job_id=job.id,
        name="artifact",
        base_model="base-model",
        gguf_uri="s3://models/a/gguf",
        ollama_model_tag="local/artifact",
        export_status=None,
    )
    defaults.update(over)
    artifact = ModelArtifact(**defaults)
    db.add(artifact)
    await db.flush()
    return artifact


# ---- 1. delete_training vs. an in-flight export ------------------------------


@pytest.mark.parametrize("export_status", [JobStatus.PENDING, JobStatus.RUNNING])
async def test_delete_training_refuses_while_its_artifact_export_is_in_flight(
    db: AsyncSession, fake_minio: _FakeMinio, fake_ollama, export_status: JobStatus
):
    """A COMPLETED run whose artifact export is PENDING/RUNNING must 409.

    `delete_model` already refuses this exact state. Without the same guard
    on the training side, `DELETE /trainings/{id}` purges the artifact (MinIO
    objects, ollama tag, DB row) out from under a live Celery export task —
    the guard on the sibling endpoint would be bypassable just by deleting
    the parent instead.
    """
    project, dataset = await _seed(db)
    job = await _seed_job(db, project, dataset, status=JobStatus.COMPLETED)
    artifact = await _seed_artifact(
        db, job, export_status=export_status, export_celery_task_id="export-task"
    )
    await db.commit()
    # Capture ids up front: `rollback()` below expires the instances, and
    # re-reading an expired attribute would trigger a lazy refresh outside
    # the async greenlet context.
    job_id, artifact_id = job.id, artifact.id

    with pytest.raises(HTTPException) as exc:
        await trainings_service.delete_training(db, job_id, None)
    assert exc.value.status_code == 409
    assert "export" in str(exc.value.detail).lower()

    await db.rollback()
    # Nothing was purged: the artifact row, its ollama tag and its MinIO
    # objects all survive the refused call.
    assert await db.get(ModelArtifact, artifact_id) is not None
    assert await db.get(TrainingJob, job_id) is not None
    assert fake_ollama.calls == []
    assert fake_minio.removed == []


async def test_delete_training_allows_terminal_export(
    db: AsyncSession, fake_minio: _FakeMinio, fake_ollama
):
    """The 409 above is scoped to in-flight only — a FAILED (terminal) export
    must still be deletable, same "terminal is fine" rule `delete_model` uses.
    """
    project, dataset = await _seed(db)
    job = await _seed_job(db, project, dataset, status=JobStatus.COMPLETED)
    artifact = await _seed_artifact(db, job, export_status=JobStatus.FAILED)
    await db.commit()

    await trainings_service.delete_training(db, job.id, None)

    assert await db.get(ModelArtifact, artifact.id) is None
    assert await db.get(TrainingJob, job.id) is None
    assert fake_ollama.calls == ["local/artifact"]


# ---- 2. double delete --------------------------------------------------------


async def test_delete_model_twice_404s_on_the_second_call(
    db: AsyncSession, fake_minio: _FakeMinio, fake_ollama
):
    """Stale UI / double-click: the second DELETE must be a clean 404, not a
    500 from re-purging an already-gone row (and must not re-hit Ollama).
    """
    project, dataset = await _seed(db)
    job = await _seed_job(db, project, dataset)
    artifact = await _seed_artifact(db, job)
    await db.commit()

    await model_service.delete_model(db, artifact.id, None)
    assert fake_ollama.calls == ["local/artifact"]

    with pytest.raises(HTTPException) as exc:
        await model_service.delete_model(db, artifact.id, None)
    assert exc.value.status_code == 404
    # No second purge attempt against the external systems.
    assert fake_ollama.calls == ["local/artifact"]


async def test_delete_training_twice_404s_on_the_second_call(
    db: AsyncSession, fake_minio: _FakeMinio, fake_ollama
):
    project, dataset = await _seed(db)
    job = await _seed_job(db, project, dataset)
    await _seed_artifact(db, job)
    await db.commit()

    await trainings_service.delete_training(db, job.id, None)

    with pytest.raises(HTTPException) as exc:
        await trainings_service.delete_training(db, job.id, None)
    assert exc.value.status_code == 404


# ---- 3. rename to the same name ---------------------------------------------


async def test_rename_dataset_to_its_own_current_name_succeeds(db: AsyncSession):
    """`PATCH /datasets/{id}` with the name it already has: a plain success,
    not a spurious 409/422, and the audit row records old == new rather than
    being skipped.
    """
    _project, dataset = await _seed(db)
    await db.commit()

    resp = await datasets_service.rename_dataset(db, dataset.id, DatasetUpdate(name="ds"), None)
    assert resp.name == "ds"

    rows = (
        (await db.execute(select(AuditEvent).where(AuditEvent.action == "dataset.rename")))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].event_metadata == {"old_name": "ds", "new_name": "ds"}


async def test_rename_dataset_rejects_extra_fields(db: AsyncSession):
    """`extra="forbid"` — a caller must not be able to smuggle `project_id`
    (re-parenting) through the rename body.
    """
    with pytest.raises(Exception) as exc:
        DatasetUpdate(name="x", project_id=str(uuid4()))
    assert "extra" in str(exc.value).lower() or "forbidden" in str(exc.value).lower()


# ---- 4. orphaned trainings ---------------------------------------------------


async def test_orphaned_training_is_listed_when_auth_off_and_hidden_when_on(db: AsyncSession):
    """`project_id IS NULL` run (its Project was deleted, migration 0012):

      * auth OFF (`user=None`)  -> listed, `project_id` serialises as None.
        This is the playground/join path — an orphan must stay reachable.
      * auth ON                 -> excluded (fail-closed; the LEFT OUTER JOIN
        yields `Project.owner_id IS NULL`, which never equals `user.id`).
    """
    project, dataset = await _seed(db, owner_id="user-a")
    owned = await _seed_job(db, project, dataset)
    orphan = await _seed_job(db, None, dataset, celery_task_id="orphan-task")
    await db.commit()

    anon = await trainings_service.list_trainings(
        db, project_id=None, status_filter=None, limit=50, offset=0, user=None
    )
    ids = {item.id for item in anon.items}
    assert orphan.id in ids and owned.id in ids
    assert anon.total == 2
    assert next(i for i in anon.items if i.id == orphan.id).project_id is None

    scoped = await trainings_service.list_trainings(
        db,
        project_id=None,
        status_filter=None,
        limit=50,
        offset=0,
        user=CurrentUser(id="user-a", email=None),
    )
    assert {item.id for item in scoped.items} == {owned.id}
    assert scoped.total == 1


async def test_orphaned_training_single_row_access_fails_closed_under_auth(db: AsyncSession):
    _project, dataset = await _seed(db, owner_id="user-a")
    orphan = await _seed_job(db, None, dataset)
    await db.commit()

    # auth off: reachable (existence-only).
    assert (await ownership.assert_training_access(db, orphan.id, None)).id == orphan.id

    # auth on: 403, not 404 — the row exists, the caller just can't have it.
    with pytest.raises(HTTPException) as exc:
        await ownership.assert_training_access(db, orphan.id, CurrentUser(id="user-a", email=None))
    assert exc.value.status_code == 403


async def test_orphaned_training_still_blocks_its_training_name(db: AsyncSession):
    """Name uniqueness must survive orphaning — the MLflow run name / Ollama
    tag an orphan minted is still taken.
    """
    from api.services.training_service import _assert_training_name_available

    project, dataset = await _seed(db)
    await _seed_job(db, None, dataset, training_name="taken-v1")
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await _assert_training_name_available(db, project=project, training_name="taken-v1")
    assert exc.value.status_code == 409


# ---- 5. delete_training's post-delete audit row ------------------------------


async def test_delete_training_audit_row_lands_with_the_deleted_rows_ids(
    db: AsyncSession, fake_minio: _FakeMinio, fake_ollama
):
    """`audit_service.record(...)` runs AFTER `await db.delete(job)` and reads
    `job.id` / `job.project_id` / `job.celery_task_id` off the pending-delete
    instance. Pin that the row really commits with those values (a flush-order
    or expired-attribute regression would show up as a missing row or a
    None/DetachedInstanceError here).
    """
    project, dataset = await _seed(db)
    job = await _seed_job(db, project, dataset, celery_task_id="train-xyz")
    await db.commit()
    job_id, project_id = job.id, project.id

    await trainings_service.delete_training(db, job_id, None)

    rows = (
        (await db.execute(select(AuditEvent).where(AuditEvent.action == "training.delete")))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].resource_id == str(job_id)
    assert rows[0].project_id == project_id
    assert rows[0].event_metadata == {"job_id": "train-xyz"}
    assert await db.get(TrainingJob, job_id) is None
