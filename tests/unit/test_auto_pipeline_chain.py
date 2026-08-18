"""Unit tests for W2-T1's `enqueue_auto_pipeline` — the plain function that
wires the export->evaluate Celery chain together.

Scope: this file is about the WIRING decision (does `enqueue_auto_pipeline`
enqueue `model.export` with the right kwargs, and hand it the right `link=`/
`link_error=` continuation signatures for each combination of
`auto_export`/`auto_evaluate`), not about what `pipeline.auto_evaluate`
itself does once it runs — that's `tests/unit/test_auto_pipeline_evaluate.py`.

No Celery broker/backend is touched: `workers.tasks.model_export.export_model`
is replaced with a tiny recording fake exposing just the `.si(**kwargs)` /
`.apply_async(**opts)` surface `enqueue_auto_pipeline` actually calls, and
`auto_evaluate` / `finalize_export_only` / `mark_export_failed` (the real,
`celery_app`-registered tasks this module builds signatures from) are left
untouched — `.si()`/`.s()` signature construction is pure/local and doesn't
touch a broker, so their real objects are used and inspected via the
`Signature.task` / `.kwargs` / `.immutable` properties Celery exposes.

Uses the same in-memory-sqlite + JSONB `@compiles` shim pattern as
`tests/unit/test_dataset_decouple_auto_pipeline.py` and the same
`session_scope` monkeypatch shape as
`tests/unit/test_worker_orphan_cleanup_export.py`.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

import workers.tasks.auto_pipeline as auto_pipeline
import workers.tasks.model_export as model_export_module
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.enums import DatasetSource, JobStatus, TaskType, TrainingMode


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


# ---- fakes ------------------------------------------------------------------


class _FakeSignature:
    """Records what `.apply_async(...)` was called with; nothing executes."""

    def __init__(self, kwargs: dict) -> None:
        self.kwargs = kwargs
        self.apply_async_calls: list[dict] = []

    def apply_async(self, **opts):
        self.apply_async_calls.append(opts)
        return SimpleNamespace(id="fake-export-task-id")


class _FakeExportTask:
    """Stand-in for `workers.tasks.model_export.export_model` — records every
    `.si(**kwargs)` call instead of building a real Celery signature."""

    def __init__(self) -> None:
        self.si_calls: list[_FakeSignature] = []

    def si(self, **kwargs):
        sig = _FakeSignature(kwargs)
        self.si_calls.append(sig)
        return sig


# ---- fixtures -----------------------------------------------------------


@pytest.fixture
def sync_sessionmaker():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    yield maker
    engine.dispose()


@pytest.fixture
def fake_export_task(monkeypatch: pytest.MonkeyPatch) -> _FakeExportTask:
    fake = _FakeExportTask()
    monkeypatch.setattr(model_export_module, "export_model", fake)
    return fake


@pytest.fixture(autouse=True)
def _patch_session_scope(monkeypatch: pytest.MonkeyPatch, sync_sessionmaker):
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

    monkeypatch.setattr(auto_pipeline, "session_scope", _fake_session_scope)


def _make_training_job(
    sync_sessionmaker, *, auto_export: bool, auto_evaluate: bool
) -> str:
    session = sync_sessionmaker()
    try:
        project = Project(id=uuid4(), name="proj", task_type=TaskType.QA)
        dataset = Dataset(
            id=uuid4(),
            project_id=project.id,
            name="ds",
            task_type=TaskType.QA,
            source=DatasetSource.SEED,
            status=JobStatus.COMPLETED,
            num_samples=10,
            storage_uri="s3://bucket/ds.jsonl",
        )
        job = TrainingJob(
            id=uuid4(),
            project_id=project.id,
            dataset_id=dataset.id,
            mode=TrainingMode.MANUAL,
            status=JobStatus.COMPLETED,
            base_model="Qwen/Qwen2.5-0.5B",
            config_json={"epochs": 1},
            auto_export=auto_export,
            auto_evaluate=auto_evaluate,
        )
        session.add_all([project, dataset, job])
        session.commit()
        return str(job.id)
    finally:
        session.close()


# ---- tests --------------------------------------------------------------


class TestEnqueueAutoPipelineNoOps:
    def test_auto_export_false_never_touches_export_or_auto_pipeline(
        self, sync_sessionmaker, fake_export_task
    ) -> None:
        training_id = _make_training_job(
            sync_sessionmaker, auto_export=False, auto_evaluate=True
        )
        artifact_id = str(uuid4())

        auto_pipeline.enqueue_auto_pipeline(training_id=training_id, artifact_id=artifact_id)

        assert fake_export_task.si_calls == []

        session = sync_sessionmaker()
        try:
            job = session.get(TrainingJob, UUID(training_id))
            assert job.auto_pipeline is None
        finally:
            session.close()

    def test_missing_training_job_is_a_silent_noop(
        self, sync_sessionmaker, fake_export_task
    ) -> None:
        auto_pipeline.enqueue_auto_pipeline(
            training_id=str(uuid4()), artifact_id=str(uuid4())
        )
        assert fake_export_task.si_calls == []


class TestEnqueueAutoPipelineExportOnly:
    def test_auto_evaluate_false_enqueues_export_with_finalize_export_only_link(
        self, sync_sessionmaker, fake_export_task
    ) -> None:
        training_id = _make_training_job(
            sync_sessionmaker, auto_export=True, auto_evaluate=False
        )
        artifact_id = str(uuid4())

        auto_pipeline.enqueue_auto_pipeline(training_id=training_id, artifact_id=artifact_id)

        assert len(fake_export_task.si_calls) == 1
        sig = fake_export_task.si_calls[0]
        assert sig.kwargs == {
            "artifact_id": artifact_id,
            "format": "gguf",
            "quantization": "q4_k_m",
        }

        assert len(sig.apply_async_calls) == 1
        opts = sig.apply_async_calls[0]
        link = opts["link"]
        link_error = opts["link_error"]

        assert link.task == "pipeline.finalize_export_only"
        assert link.kwargs == {"training_id": training_id, "artifact_id": artifact_id}
        assert link.immutable is True  # discards export_model's return value

        assert link_error.task == "pipeline.mark_export_failed"
        assert link_error.kwargs == {"training_id": training_id, "artifact_id": artifact_id}
        assert link_error.immutable is False  # needs the failed task's id prepended

    def test_seeds_auto_pipeline_state_export_only(
        self, sync_sessionmaker, fake_export_task
    ) -> None:
        training_id = _make_training_job(
            sync_sessionmaker, auto_export=True, auto_evaluate=False
        )
        artifact_id = str(uuid4())

        auto_pipeline.enqueue_auto_pipeline(training_id=training_id, artifact_id=artifact_id)

        session = sync_sessionmaker()
        try:
            job = session.get(TrainingJob, UUID(training_id))
            assert job.auto_pipeline["export"] == {
                "status": "running",
                "artifact_id": artifact_id,
                "error": None,
            }
            assert job.auto_pipeline["evaluate"]["status"] == "skipped"
            assert job.auto_pipeline["evaluate"]["skip_reason"] == "auto_evaluate is false"
        finally:
            session.close()


class TestEnqueueAutoPipelineFullChain:
    def test_auto_evaluate_true_enqueues_export_with_auto_evaluate_link(
        self, sync_sessionmaker, fake_export_task
    ) -> None:
        training_id = _make_training_job(
            sync_sessionmaker, auto_export=True, auto_evaluate=True
        )
        artifact_id = str(uuid4())

        auto_pipeline.enqueue_auto_pipeline(training_id=training_id, artifact_id=artifact_id)

        assert len(fake_export_task.si_calls) == 1
        sig = fake_export_task.si_calls[0]
        opts = sig.apply_async_calls[0]
        link = opts["link"]
        link_error = opts["link_error"]

        assert link.task == "pipeline.auto_evaluate"
        assert link.kwargs == {"training_id": training_id, "artifact_id": artifact_id}
        assert link.immutable is True

        assert link_error.task == "pipeline.mark_export_failed"

    def test_seeds_auto_pipeline_state_full_chain(
        self, sync_sessionmaker, fake_export_task
    ) -> None:
        training_id = _make_training_job(
            sync_sessionmaker, auto_export=True, auto_evaluate=True
        )
        artifact_id = str(uuid4())

        auto_pipeline.enqueue_auto_pipeline(training_id=training_id, artifact_id=artifact_id)

        session = sync_sessionmaker()
        try:
            job = session.get(TrainingJob, UUID(training_id))
            assert job.auto_pipeline["export"]["status"] == "running"
            assert job.auto_pipeline["evaluate"] == {
                "status": "pending",
                "evaluation_id": None,
                "skip_reason": None,
                "error": None,
            }
        finally:
            session.close()
