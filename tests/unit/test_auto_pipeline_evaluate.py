"""Unit tests for W2-T1's `pipeline.auto_evaluate` task and its sibling
stage-bookkeeping callbacks (`mark_export_failed`, `finalize_export_only`,
`mark_evaluate_completed`, `mark_evaluate_failed`) in
`workers/tasks/auto_pipeline.py`.

Covers, per the task brief:
  - holdout resolution query (`Dataset.parent_dataset_id == TrainingJob.
    dataset_id AND generation_metadata['role'] == 'holdout'`) picks the
    right row and ignores unrelated datasets.
  - skip reason persisted (with no EvaluationRun created, no
    `evaluation.run` enqueued) when no holdout dataset exists.
  - qa -> `use_llm_judge=True` with `settings.llm_judge_model`; classification
    / tool_calling -> `use_llm_judge=False`.
  - export failure (`mark_export_failed`) marks the export stage failed and
    the evaluate stage skipped, without ever creating an EvaluationRun or
    calling `pipeline.auto_evaluate`'s body at all.

Same in-memory-sqlite + JSONB `@compiles` shim and `session_scope`
monkeypatch pattern as `tests/unit/test_auto_pipeline_chain.py` /
`tests/unit/test_worker_orphan_cleanup_export.py`. `workers.tasks.evaluation.
run_evaluation` is replaced with a tiny recording fake — real evaluation
requires a reachable Ollama daemon and is out of scope here; this file is
about what `auto_evaluate` decides to enqueue and how it books state, not
about evaluation itself (covered elsewhere, e.g.
`tests/unit/test_eval_judge_usage.py`).
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
import workers.tasks.evaluation as evaluation_module
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.evaluation_run import EvaluationRun
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.enums import DatasetSource, JobStatus, TaskType, TrainingMode


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


# ---- fakes ------------------------------------------------------------------


class _FakeRunEvaluationTask:
    """Stand-in for `workers.tasks.evaluation.run_evaluation` — records the
    `.apply_async(kwargs=..., link=..., link_error=...)` call instead of
    actually running inference against Ollama."""

    def __init__(self) -> None:
        self.apply_async_calls: list[dict] = []

    def apply_async(self, **opts):
        self.apply_async_calls.append(opts)
        return SimpleNamespace(id="fake-eval-task-id")


# ---- fixtures -----------------------------------------------------------


@pytest.fixture
def sync_sessionmaker():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    yield maker
    engine.dispose()


@pytest.fixture
def fake_run_evaluation(monkeypatch: pytest.MonkeyPatch) -> _FakeRunEvaluationTask:
    fake = _FakeRunEvaluationTask()
    monkeypatch.setattr(evaluation_module, "run_evaluation", fake)
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


def _get_job(sync_sessionmaker, training_id: str) -> TrainingJob:
    session = sync_sessionmaker()
    try:
        return session.get(TrainingJob, UUID(training_id))
    finally:
        session.close()


class _Fixture:
    """Bag of ids for one project/dataset/training/artifact set."""

    def __init__(self, *, project_id, train_dataset_id, training_id, artifact_id):
        self.project_id = project_id
        self.train_dataset_id = train_dataset_id
        self.training_id = training_id
        self.artifact_id = artifact_id


def _make_base_fixture(
    sync_sessionmaker,
    *,
    task_type: TaskType = TaskType.QA,
    with_ollama_tag: bool = True,
) -> _Fixture:
    session = sync_sessionmaker()
    try:
        project = Project(id=uuid4(), name="proj", task_type=task_type)
        train_dataset = Dataset(
            id=uuid4(),
            project_id=project.id,
            name="train-ds",
            task_type=task_type,
            source=DatasetSource.SDG,
            status=JobStatus.COMPLETED,
            num_samples=100,
            storage_uri="s3://bucket/train.jsonl",
            generation_metadata={"role": "train"},
        )
        job = TrainingJob(
            id=uuid4(),
            project_id=project.id,
            dataset_id=train_dataset.id,
            mode=TrainingMode.MANUAL,
            status=JobStatus.COMPLETED,
            base_model="Qwen/Qwen2.5-0.5B",
            config_json={"epochs": 1},
            auto_export=True,
            auto_evaluate=True,
        )
        artifact = ModelArtifact(
            id=uuid4(),
            training_job_id=job.id,
            name="art",
            base_model="Qwen/Qwen2.5-0.5B",
            lora_adapter_uri="s3://bucket/adapters/x",
            gguf_uri="s3://bucket/exports/x/gguf",
            ollama_model_tag="local/art" if with_ollama_tag else None,
        )
        session.add_all([project, train_dataset, job, artifact])
        session.commit()
        return _Fixture(
            project_id=project.id,
            train_dataset_id=train_dataset.id,
            training_id=str(job.id),
            artifact_id=str(artifact.id),
        )
    finally:
        session.close()


def _add_holdout_dataset(
    sync_sessionmaker,
    fixture: _Fixture,
    *,
    task_type: TaskType = TaskType.QA,
    storage_uri: str | None = "s3://bucket/holdout.jsonl",
    num_samples: int = 20,
) -> None:
    session = sync_sessionmaker()
    try:
        holdout = Dataset(
            id=uuid4(),
            project_id=fixture.project_id,
            name="holdout-ds",
            task_type=task_type,
            source=DatasetSource.SDG,
            status=JobStatus.COMPLETED,
            num_samples=num_samples,
            storage_uri=storage_uri,
            parent_dataset_id=fixture.train_dataset_id,
            generation_metadata={"role": "holdout"},
        )
        session.add(holdout)
        session.commit()
    finally:
        session.close()


# ---- holdout resolution ------------------------------------------------


class TestHoldoutResolution:
    def test_finds_holdout_child_and_enqueues_evaluation(
        self, sync_sessionmaker, fake_run_evaluation
    ) -> None:
        fixture = _make_base_fixture(sync_sessionmaker, task_type=TaskType.QA)
        _add_holdout_dataset(sync_sessionmaker, fixture, task_type=TaskType.QA)

        result = auto_pipeline.auto_evaluate(
            training_id=fixture.training_id, artifact_id=fixture.artifact_id
        )

        assert result["status"] == "submitted"
        assert len(fake_run_evaluation.apply_async_calls) == 1

        session = sync_sessionmaker()
        try:
            runs = session.query(EvaluationRun).all()
            assert len(runs) == 1
            assert str(runs[0].dataset_id) == str(
                _get_holdout_id(sync_sessionmaker, fixture)
            )
        finally:
            session.close()

    def test_ignores_unrelated_datasets_with_holdout_role_for_other_parents(
        self, sync_sessionmaker, fake_run_evaluation
    ) -> None:
        fixture = _make_base_fixture(sync_sessionmaker)
        # A holdout dataset that is a child of a DIFFERENT parent dataset —
        # must not be picked up for this training's evaluate stage.
        session = sync_sessionmaker()
        try:
            other_parent = Dataset(
                id=uuid4(),
                project_id=fixture.project_id,
                name="other-parent",
                task_type=TaskType.QA,
                source=DatasetSource.SDG,
                status=JobStatus.COMPLETED,
                num_samples=5,
                storage_uri="s3://bucket/other.jsonl",
            )
            session.add(other_parent)
            session.commit()
            unrelated_holdout = Dataset(
                id=uuid4(),
                project_id=fixture.project_id,
                name="unrelated-holdout",
                task_type=TaskType.QA,
                source=DatasetSource.SDG,
                status=JobStatus.COMPLETED,
                num_samples=5,
                storage_uri="s3://bucket/unrelated-holdout.jsonl",
                parent_dataset_id=other_parent.id,
                generation_metadata={"role": "holdout"},
            )
            session.add(unrelated_holdout)
            session.commit()
        finally:
            session.close()

        result = auto_pipeline.auto_evaluate(
            training_id=fixture.training_id, artifact_id=fixture.artifact_id
        )

        assert result["status"] == "skipped"
        assert fake_run_evaluation.apply_async_calls == []


def _get_holdout_id(sync_sessionmaker, fixture: _Fixture):
    session = sync_sessionmaker()
    try:
        row = (
            session.query(Dataset)
            .filter(Dataset.parent_dataset_id == fixture.train_dataset_id)
            .first()
        )
        return row.id
    finally:
        session.close()


# ---- skip reasons ---------------------------------------------------------


class TestSkipReasons:
    def test_no_holdout_dataset_persists_skip_reason_and_creates_no_run(
        self, sync_sessionmaker, fake_run_evaluation
    ) -> None:
        fixture = _make_base_fixture(sync_sessionmaker)
        # No holdout dataset added at all.

        result = auto_pipeline.auto_evaluate(
            training_id=fixture.training_id, artifact_id=fixture.artifact_id
        )

        assert result["status"] == "skipped"
        assert "no holdout" in result["skip_reason"].lower()
        assert fake_run_evaluation.apply_async_calls == []

        job = _get_job(sync_sessionmaker, fixture.training_id)
        assert job.auto_pipeline["evaluate"]["status"] == "skipped"
        assert job.auto_pipeline["evaluate"]["skip_reason"] == result["skip_reason"]
        assert job.auto_pipeline["export"]["status"] == "completed"

        session = sync_sessionmaker()
        try:
            assert session.query(EvaluationRun).count() == 0
        finally:
            session.close()

    def test_holdout_without_rows_persists_skip_reason(
        self, sync_sessionmaker, fake_run_evaluation
    ) -> None:
        fixture = _make_base_fixture(sync_sessionmaker)
        _add_holdout_dataset(sync_sessionmaker, fixture, storage_uri=None, num_samples=0)

        result = auto_pipeline.auto_evaluate(
            training_id=fixture.training_id, artifact_id=fixture.artifact_id
        )

        assert result["status"] == "skipped"
        assert "no rows persisted" in result["skip_reason"].lower()
        assert fake_run_evaluation.apply_async_calls == []

    def test_missing_ollama_tag_persists_skip_reason(
        self, sync_sessionmaker, fake_run_evaluation
    ) -> None:
        fixture = _make_base_fixture(sync_sessionmaker, with_ollama_tag=False)
        _add_holdout_dataset(sync_sessionmaker, fixture)

        result = auto_pipeline.auto_evaluate(
            training_id=fixture.training_id, artifact_id=fixture.artifact_id
        )

        assert result["status"] == "skipped"
        assert "ollama_model_tag" in result["skip_reason"]
        assert fake_run_evaluation.apply_async_calls == []


# ---- task-type dispatch ----------------------------------------------------


class TestTaskTypeDispatch:
    def test_qa_uses_llm_judge_with_configured_judge_model(
        self, sync_sessionmaker, fake_run_evaluation
    ) -> None:
        fixture = _make_base_fixture(sync_sessionmaker, task_type=TaskType.QA)
        _add_holdout_dataset(sync_sessionmaker, fixture, task_type=TaskType.QA)

        auto_pipeline.auto_evaluate(
            training_id=fixture.training_id, artifact_id=fixture.artifact_id
        )

        from api.core.config import get_settings

        kwargs = fake_run_evaluation.apply_async_calls[0]["kwargs"]
        assert kwargs["use_llm_judge"] is True
        assert kwargs["judge_model"] == get_settings().llm_judge_model

    @pytest.mark.parametrize("task_type", [TaskType.CLASSIFICATION, TaskType.TOOL_CALLING])
    def test_non_qa_does_not_use_llm_judge(
        self, sync_sessionmaker, fake_run_evaluation, task_type
    ) -> None:
        fixture = _make_base_fixture(sync_sessionmaker, task_type=task_type)
        _add_holdout_dataset(sync_sessionmaker, fixture, task_type=task_type)

        auto_pipeline.auto_evaluate(
            training_id=fixture.training_id, artifact_id=fixture.artifact_id
        )

        kwargs = fake_run_evaluation.apply_async_calls[0]["kwargs"]
        assert kwargs["use_llm_judge"] is False
        assert kwargs["judge_model"] is None


# ---- export failure blocks evaluate ---------------------------------------


class TestExportFailureBlocksEvaluate:
    def test_mark_export_failed_sets_export_failed_and_evaluate_skipped(
        self, sync_sessionmaker, fake_run_evaluation, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fixture = _make_base_fixture(sync_sessionmaker)
        _add_holdout_dataset(sync_sessionmaker, fixture)

        class _FakeAsyncResult:
            def __init__(self, task_id, app=None):
                self.result = RuntimeError("GPU OOM during export")

        monkeypatch.setattr(auto_pipeline, "AsyncResult", _FakeAsyncResult)

        result = auto_pipeline.mark_export_failed(
            "some-failed-export-task-id",
            training_id=fixture.training_id,
            artifact_id=fixture.artifact_id,
        )

        assert result["status"] == "failed"

        job = _get_job(sync_sessionmaker, fixture.training_id)
        assert job.auto_pipeline["export"]["status"] == "failed"
        assert "GPU OOM" in job.auto_pipeline["export"]["error"]
        assert job.auto_pipeline["evaluate"]["status"] == "skipped"
        assert "export failed" in job.auto_pipeline["evaluate"]["skip_reason"]

        # The evaluate stage's own logic (holdout resolution, EvaluationRun
        # creation) never ran on this path — `auto_evaluate` was never
        # called, so nothing was ever enqueued for evaluation.
        assert fake_run_evaluation.apply_async_calls == []
        session = sync_sessionmaker()
        try:
            assert session.query(EvaluationRun).count() == 0
        finally:
            session.close()

    def test_resolve_failure_message_falls_back_when_backend_read_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*args, **kwargs):
            raise RuntimeError("no result backend configured")

        monkeypatch.setattr(auto_pipeline, "AsyncResult", _boom)
        msg = auto_pipeline._resolve_failure_message("whatever-id")
        assert "task failed" in msg


# ---- export-only finalize + evaluate completion/failure callbacks --------


class TestFinalizeExportOnly:
    def test_marks_export_completed(self, sync_sessionmaker) -> None:
        fixture = _make_base_fixture(sync_sessionmaker)

        result = auto_pipeline.finalize_export_only(
            training_id=fixture.training_id, artifact_id=fixture.artifact_id
        )

        assert result["status"] == "completed"
        job = _get_job(sync_sessionmaker, fixture.training_id)
        assert job.auto_pipeline["export"]["status"] == "completed"


class TestEvaluateCompletionCallbacks:
    def test_mark_evaluate_completed_normal_success(self, sync_sessionmaker) -> None:
        fixture = _make_base_fixture(sync_sessionmaker)

        auto_pipeline.mark_evaluate_completed(
            {"status": "completed", "evaluation_id": "x"},
            training_id=fixture.training_id,
        )

        job = _get_job(sync_sessionmaker, fixture.training_id)
        assert job.auto_pipeline["evaluate"]["status"] == "completed"

    def test_mark_evaluate_completed_maps_cancelled_result_to_skipped(
        self, sync_sessionmaker
    ) -> None:
        fixture = _make_base_fixture(sync_sessionmaker)

        auto_pipeline.mark_evaluate_completed(
            {"status": "cancelled", "evaluation_id": "x"},
            training_id=fixture.training_id,
        )

        job = _get_job(sync_sessionmaker, fixture.training_id)
        assert job.auto_pipeline["evaluate"]["status"] == "skipped"
        assert job.auto_pipeline["evaluate"]["skip_reason"] == "evaluation was cancelled"

    def test_mark_evaluate_failed_sets_failed_with_message(
        self, sync_sessionmaker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fixture = _make_base_fixture(sync_sessionmaker)

        class _FakeAsyncResult:
            def __init__(self, task_id, app=None):
                self.result = RuntimeError("Ollama unreachable")

        monkeypatch.setattr(auto_pipeline, "AsyncResult", _FakeAsyncResult)

        auto_pipeline.mark_evaluate_failed(
            "some-failed-eval-task-id", training_id=fixture.training_id
        )

        job = _get_job(sync_sessionmaker, fixture.training_id)
        assert job.auto_pipeline["evaluate"]["status"] == "failed"
        assert "Ollama unreachable" in job.auto_pipeline["evaluate"]["error"]
