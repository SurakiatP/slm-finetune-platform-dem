"""Unit tests for T-B2 (blocker #11): `auto_pipeline.sync_export_success` and
`model_export._training_id_for_artifact`.

`sync_export_success` is the bookkeeping hook called from `export_model`'s
success path (see `workers/tasks/model_export.py`, right after the export
commit). It mirrors a successful export onto `TrainingJob.auto_pipeline` and,
when this export was a *retry* of a leg that had previously failed via the
auto-pipeline (export failed -> evaluate auto-skipped with the fixed
`_EXPORT_FAILED_SKIP_REASON`), resumes the auto-evaluate leg by re-enqueuing
`pipeline.auto_evaluate` — without reimplementing any of `auto_evaluate`'s own
holdout-resolution/EvaluationRun logic.

Same in-memory-sqlite + JSONB `@compiles` shim, `session_scope` monkeypatch,
and fixture-building pattern as `tests/unit/test_auto_pipeline_evaluate.py`.
Tests call `sync_export_success` directly — `export_model` itself (GPU/
Unsloth/MinIO/Ollama) is never booted here.
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
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.enums import DatasetSource, JobStatus, TaskType, TrainingMode


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


# ---- fakes ------------------------------------------------------------------


class _FakeAutoEvaluateTask:
    """Stand-in for `workers.tasks.auto_pipeline.auto_evaluate` — records the
    `.apply_async(kwargs=...)` call instead of actually running the evaluate
    leg (covered by `tests/unit/test_auto_pipeline_evaluate.py`)."""

    def __init__(self) -> None:
        self.apply_async_calls: list[dict] = []

    def apply_async(self, **opts):
        self.apply_async_calls.append(opts)
        return SimpleNamespace(id="fake-auto-evaluate-task-id")


# ---- fixtures -----------------------------------------------------------


@pytest.fixture
def sync_sessionmaker():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    yield maker
    engine.dispose()


@pytest.fixture
def fake_auto_evaluate(monkeypatch: pytest.MonkeyPatch) -> _FakeAutoEvaluateTask:
    fake = _FakeAutoEvaluateTask()
    monkeypatch.setattr(auto_pipeline, "auto_evaluate", fake)
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
    auto_evaluate: bool = True,
    auto_pipeline_blob: dict | None,
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
            auto_evaluate=auto_evaluate,
            auto_pipeline=auto_pipeline_blob,
        )
        artifact = ModelArtifact(
            id=uuid4(),
            training_job_id=job.id,
            name="art",
            base_model="Qwen/Qwen2.5-0.5B",
            lora_adapter_uri="s3://bucket/adapters/x",
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


# ---- (a)/(b): no-op / scope-guard cases ------------------------------------


class TestNoOpAndScopeGuard:
    def test_no_auto_pipeline_blob_returns_no_pipeline(
        self, sync_sessionmaker, fake_auto_evaluate
    ) -> None:
        fixture = _make_base_fixture(sync_sessionmaker, auto_pipeline_blob=None)

        result = auto_pipeline.sync_export_success(
            artifact_id=fixture.artifact_id, training_id=fixture.training_id
        )

        assert result == "no_pipeline"
        assert fake_auto_evaluate.apply_async_calls == []
        job = _get_job(sync_sessionmaker, fixture.training_id)
        assert job.auto_pipeline is None

    def test_different_export_artifact_id_is_scope_guarded(
        self, sync_sessionmaker, fake_auto_evaluate
    ) -> None:
        blob = {
            "export": {"status": "running", "artifact_id": "some-other-artifact", "error": None},
            "evaluate": {
                "status": "pending",
                "evaluation_id": None,
                "skip_reason": None,
                "error": None,
            },
        }
        fixture = _make_base_fixture(sync_sessionmaker, auto_pipeline_blob=blob)

        result = auto_pipeline.sync_export_success(
            artifact_id=fixture.artifact_id, training_id=fixture.training_id
        )

        assert result == "no_pipeline"
        assert fake_auto_evaluate.apply_async_calls == []
        job = _get_job(sync_sessionmaker, fixture.training_id)
        # Blob is byte-for-byte untouched — this is the manual-export scope
        # guard: a manual export of a *different* artifact must never mutate
        # an unrelated auto-pipeline's bookkeeping.
        assert job.auto_pipeline == blob


# ---- (c)/(d)/(e): synced (no resume) cases ---------------------------------


class TestSyncedNoResume:
    def test_live_chain_export_running_syncs_to_completed_no_enqueue(
        self, sync_sessionmaker, fake_auto_evaluate
    ) -> None:
        blob = {
            "export": {"status": "running", "artifact_id": None, "error": None},
            "evaluate": {
                "status": "pending",
                "evaluation_id": None,
                "skip_reason": None,
                "error": None,
            },
        }
        fixture = _make_base_fixture(sync_sessionmaker, auto_pipeline_blob=blob)
        blob["export"]["artifact_id"] = fixture.artifact_id
        job = _get_job(sync_sessionmaker, fixture.training_id)
        job.auto_pipeline = blob
        session = sync_sessionmaker()
        try:
            session.add(job)
            session.commit()
        finally:
            session.close()

        result = auto_pipeline.sync_export_success(
            artifact_id=fixture.artifact_id, training_id=fixture.training_id
        )

        assert result == "synced"
        assert fake_auto_evaluate.apply_async_calls == []
        job = _get_job(sync_sessionmaker, fixture.training_id)
        assert job.auto_pipeline["export"] == {
            "status": "completed",
            "artifact_id": fixture.artifact_id,
            "error": None,
        }
        assert job.auto_pipeline["evaluate"]["status"] == "pending"

    def test_fully_completed_pipeline_manual_reexport_does_not_reenqueue(
        self, sync_sessionmaker, fake_auto_evaluate
    ) -> None:
        fixture = _make_base_fixture(sync_sessionmaker, auto_pipeline_blob=None)
        blob = {
            "export": {"status": "completed", "artifact_id": fixture.artifact_id, "error": None},
            "evaluate": {
                "status": "completed",
                "evaluation_id": "some-eval-id",
                "skip_reason": None,
                "error": None,
            },
        }
        job = _get_job(sync_sessionmaker, fixture.training_id)
        job.auto_pipeline = blob
        session = sync_sessionmaker()
        try:
            session.add(job)
            session.commit()
        finally:
            session.close()

        result = auto_pipeline.sync_export_success(
            artifact_id=fixture.artifact_id, training_id=fixture.training_id
        )

        assert result == "synced"
        assert fake_auto_evaluate.apply_async_calls == []
        job = _get_job(sync_sessionmaker, fixture.training_id)
        assert job.auto_pipeline["export"]["status"] == "completed"
        assert job.auto_pipeline["evaluate"]["status"] == "completed"
        assert job.auto_pipeline["evaluate"]["evaluation_id"] == "some-eval-id"

    def test_auto_evaluate_false_export_failed_evaluate_skipped_stays_synced(
        self, sync_sessionmaker, fake_auto_evaluate
    ) -> None:
        blob = {
            "export": {"status": "failed", "artifact_id": None, "error": "boom"},
            "evaluate": {
                "status": "skipped",
                "evaluation_id": None,
                "skip_reason": "auto_evaluate is false",
                "error": None,
            },
        }
        fixture = _make_base_fixture(
            sync_sessionmaker, auto_evaluate=False, auto_pipeline_blob=None
        )
        blob["export"]["artifact_id"] = fixture.artifact_id
        job = _get_job(sync_sessionmaker, fixture.training_id)
        job.auto_pipeline = blob
        session = sync_sessionmaker()
        try:
            session.add(job)
            session.commit()
        finally:
            session.close()

        result = auto_pipeline.sync_export_success(
            artifact_id=fixture.artifact_id, training_id=fixture.training_id
        )

        assert result == "synced"
        assert fake_auto_evaluate.apply_async_calls == []
        job = _get_job(sync_sessionmaker, fixture.training_id)
        assert job.auto_pipeline["export"]["status"] == "completed"
        # evaluate stage unchanged — auto_evaluate=False means the resume
        # predicate is never true, no matter what export/evaluate look like.
        assert job.auto_pipeline["evaluate"]["status"] == "skipped"
        assert job.auto_pipeline["evaluate"]["skip_reason"] == "auto_evaluate is false"


# ---- (f)/(g): resume cases --------------------------------------------------


class TestResume:
    def _failed_export_blob(self, artifact_id: str) -> dict:
        return {
            "export": {"status": "failed", "artifact_id": artifact_id, "error": "GPU OOM"},
            "evaluate": {
                "status": "skipped",
                "evaluation_id": None,
                "skip_reason": auto_pipeline._EXPORT_FAILED_SKIP_REASON,
                "error": None,
            },
        }

    def test_resumes_auto_evaluate_when_holdout_present(
        self, sync_sessionmaker, fake_auto_evaluate
    ) -> None:
        fixture = _make_base_fixture(
            sync_sessionmaker, auto_evaluate=True, auto_pipeline_blob=None
        )
        _add_holdout_dataset(sync_sessionmaker, fixture)
        blob = self._failed_export_blob(fixture.artifact_id)
        job = _get_job(sync_sessionmaker, fixture.training_id)
        job.auto_pipeline = blob
        session = sync_sessionmaker()
        try:
            session.add(job)
            session.commit()
        finally:
            session.close()

        result = auto_pipeline.sync_export_success(
            artifact_id=fixture.artifact_id, training_id=fixture.training_id
        )

        assert result == "resumed"
        assert len(fake_auto_evaluate.apply_async_calls) == 1
        call = fake_auto_evaluate.apply_async_calls[0]
        assert call["kwargs"] == {
            "training_id": fixture.training_id,
            "artifact_id": fixture.artifact_id,
        }

        job = _get_job(sync_sessionmaker, fixture.training_id)
        assert job.auto_pipeline["export"] == {
            "status": "completed",
            "artifact_id": fixture.artifact_id,
            "error": None,
        }
        assert job.auto_pipeline["evaluate"]["status"] == "pending"
        assert job.auto_pipeline["evaluate"]["skip_reason"] is None
        assert job.auto_pipeline["evaluate"]["evaluation_id"] is None
        assert job.auto_pipeline["evaluate"]["error"] is None

    @pytest.mark.parametrize("scenario", ["holdout_deleted", "holdout_empty"])
    def test_resume_but_holdout_gone_marks_holdout_missing(
        self, sync_sessionmaker, fake_auto_evaluate, scenario
    ) -> None:
        fixture = _make_base_fixture(
            sync_sessionmaker, auto_evaluate=True, auto_pipeline_blob=None
        )
        if scenario == "holdout_empty":
            _add_holdout_dataset(sync_sessionmaker, fixture, storage_uri=None, num_samples=0)
        # else: no holdout dataset added at all (deleted / never existed).
        blob = self._failed_export_blob(fixture.artifact_id)
        job = _get_job(sync_sessionmaker, fixture.training_id)
        job.auto_pipeline = blob
        session = sync_sessionmaker()
        try:
            session.add(job)
            session.commit()
        finally:
            session.close()

        result = auto_pipeline.sync_export_success(
            artifact_id=fixture.artifact_id, training_id=fixture.training_id
        )

        assert result == "holdout_missing"
        assert fake_auto_evaluate.apply_async_calls == []

        job = _get_job(sync_sessionmaker, fixture.training_id)
        assert job.auto_pipeline["export"]["status"] == "completed"
        assert job.auto_pipeline["evaluate"]["status"] == "skipped"
        assert "holdout dataset deleted" in job.auto_pipeline["evaluate"]["skip_reason"]
        assert job.auto_pipeline["evaluate"]["evaluation_id"] is None
        assert job.auto_pipeline["evaluate"]["error"] is None


# ---- (h): wiring test for model_export._training_id_for_artifact -----------


class TestTrainingIdForArtifactWiring:
    def test_returns_training_id_for_persisted_artifact(
        self, sync_sessionmaker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import workers.tasks.model_export as model_export

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

        monkeypatch.setattr(model_export, "session_scope", _fake_session_scope)

        fixture = _make_base_fixture(sync_sessionmaker, auto_pipeline_blob=None)
        artifact_uuid = UUID(fixture.artifact_id)

        result = model_export._training_id_for_artifact(artifact_uuid)

        assert result == fixture.training_id

    def test_returns_none_for_missing_artifact(
        self, sync_sessionmaker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import workers.tasks.model_export as model_export

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

        monkeypatch.setattr(model_export, "session_scope", _fake_session_scope)

        result = model_export._training_id_for_artifact(uuid4())

        assert result is None


# ---- review additions: ordering + the never-raises contract ------------------


class TestEnqueueHappensAfterCommit:
    """`sync_export_success` must reassign the blob and let its
    `session_scope()` commit BEFORE `auto_evaluate.apply_async` fires.

    Without that ordering a real Celery worker can start `auto_evaluate`
    against a `TrainingJob` row whose `evaluate` stage is still `skipped`
    with the export-failed reason — `auto_evaluate` would then overwrite it
    from stale state, and a rollback would leave a queued evaluate leg with
    no pipeline record of it at all. `TestResume` above asserts the final
    blob and the enqueue kwargs, but neither pins their *order*.
    """

    def test_blob_is_already_committed_when_apply_async_fires(
        self, sync_sessionmaker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fixture = _make_base_fixture(
            sync_sessionmaker, auto_evaluate=True, auto_pipeline_blob=None
        )
        _add_holdout_dataset(sync_sessionmaker, fixture)
        job = _get_job(sync_sessionmaker, fixture.training_id)
        job.auto_pipeline = {
            "export": {
                "status": "failed",
                "artifact_id": fixture.artifact_id,
                "error": "GPU OOM",
            },
            "evaluate": {
                "status": "skipped",
                "evaluation_id": None,
                "skip_reason": auto_pipeline._EXPORT_FAILED_SKIP_REASON,
                "error": None,
            },
        }
        session = sync_sessionmaker()
        try:
            session.add(job)
            session.commit()
        finally:
            session.close()

        observed: dict = {}

        class _ObservingTask:
            def apply_async(self, **opts):
                # A brand-new session, exactly like the worker that picks the
                # task up would use: it can only see committed state.
                observed["blob"] = _get_job(sync_sessionmaker, fixture.training_id).auto_pipeline
                return SimpleNamespace(id="fake-auto-evaluate-task-id")

        monkeypatch.setattr(auto_pipeline, "auto_evaluate", _ObservingTask())

        result = auto_pipeline.sync_export_success(
            artifact_id=fixture.artifact_id, training_id=fixture.training_id
        )

        assert result == "resumed"
        assert observed, "apply_async must actually have been called"
        assert observed["blob"]["evaluate"]["status"] == "pending", (
            "apply_async fired before the blob commit — a worker picking the task "
            "up immediately would still read the stale 'skipped' evaluate stage"
        )
        assert observed["blob"]["evaluate"]["skip_reason"] is None
        assert observed["blob"]["export"]["status"] == "completed"


class TestNeverRaises:
    """The hook is called from a finished export's success path. Its own
    contract is that it swallows everything and returns a status string —
    the export task's `try/except` is the second line of defence, not the
    first.
    """

    def test_malformed_training_id_returns_no_pipeline_instead_of_raising(
        self, sync_sessionmaker, fake_auto_evaluate
    ) -> None:
        # `UUID("not-a-uuid")` raises ValueError on the very first line.
        result = auto_pipeline.sync_export_success(
            artifact_id=str(uuid4()), training_id="not-a-uuid"
        )

        assert result == "no_pipeline"
        assert fake_auto_evaluate.apply_async_calls == []

    def test_enqueue_failure_is_swallowed_after_the_blob_was_committed(
        self, sync_sessionmaker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A broker outage on `apply_async` must not propagate into the
        export task. The blob write already committed, so the pipeline still
        reflects the successful export even though the evaluate leg never
        got queued."""
        fixture = _make_base_fixture(
            sync_sessionmaker, auto_evaluate=True, auto_pipeline_blob=None
        )
        _add_holdout_dataset(sync_sessionmaker, fixture)
        job = _get_job(sync_sessionmaker, fixture.training_id)
        job.auto_pipeline = {
            "export": {
                "status": "failed",
                "artifact_id": fixture.artifact_id,
                "error": "GPU OOM",
            },
            "evaluate": {
                "status": "skipped",
                "evaluation_id": None,
                "skip_reason": auto_pipeline._EXPORT_FAILED_SKIP_REASON,
                "error": None,
            },
        }
        session = sync_sessionmaker()
        try:
            session.add(job)
            session.commit()
        finally:
            session.close()

        class _BrokenBrokerTask:
            def apply_async(self, **opts):
                raise OSError("broker unreachable")

        monkeypatch.setattr(auto_pipeline, "auto_evaluate", _BrokenBrokerTask())

        result = auto_pipeline.sync_export_success(
            artifact_id=fixture.artifact_id, training_id=fixture.training_id
        )

        assert result == "no_pipeline", "the swallow-everything path returns the neutral status"
        blob = _get_job(sync_sessionmaker, fixture.training_id).auto_pipeline
        assert blob["export"]["status"] == "completed", (
            "the export-stage sync committed before the enqueue attempt and must survive it"
        )
