"""Unit tests for gap-analysis item 13 ("clean up orphaned artifacts when a
job is cancelled or fails") as it applies to `workers/tasks/training.py`.

`train_manual` uploads the trained LoRA adapter directory to MinIO
(`put_directory`, under `adapters/{training_id}/...`) BEFORE the
`ModelArtifact` row that references it is committed by `_persist_artifact`.
A cancel (SIGTERM -> `SystemExit`, caught by the existing
`except BaseException`) or an ordinary failure landing in that window used
to leave the adapter prefix orphaned in the `models` bucket forever.

The fix tracks the uploaded prefix in `uploaded_prefix` (set the instant
`put_directory` returns) and a `committed` flag (set the instant
`_persist_artifact` commits). The `except BaseException` handler deletes
`uploaded_prefix` via `remove_prefix` only when `committed` is still False.

Runs `train_manual` synchronously via `.apply(...)` against an in-memory
sync sqlite engine + `fake_minio`, mirroring the established pattern in
`tests/unit/test_worker_usage_events.py` (same `_compiles(JSONB, "sqlite")`
shim, same use-site monkeypatching of `session_scope` / `sync_redis_scope` /
`get_minio_client`).

The real `UnslothTrainer` / `mlflow_run_scope` / `make_progress_callback`
pull in torch/transformers/mlflow, which this test environment doesn't have
and doesn't need — this file is about what the *worker* does with an
already-uploaded adapter once training hands control back (or blows up), not
about training correctness. Those three are monkeypatched at the same
coarse boundary `test_worker_usage_events.py` uses for `_run_generator`.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from ai_engine.training.unsloth_trainer import TrainingResult
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.enums import DatasetSource, JobStatus, TaskType, TrainingMode


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


# =============================================================================
# Fixtures / shared helpers
# =============================================================================


@pytest.fixture
def sync_sessionmaker():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    yield maker
    engine.dispose()


class _FakeUnslothTrainer:
    """Stand-in for `ai_engine.training.unsloth_trainer.UnslothTrainer`.

    Real training is GPU/torch work, out of scope here. `.train()` writes
    one real file into `output_dir` so `put_directory` (the real function,
    unmocked) has something to actually walk and upload to `fake_minio` —
    that upload, and what happens to it on cancel/failure, is what this
    file tests.
    """

    def __init__(self, *, base_model, config, task_type, tool_definitions, output_dir):
        self.output_dir = output_dir

    def train(self, rows, callbacks=None):
        with open(os.path.join(self.output_dir, "adapter_model.bin"), "wb") as fh:
            fh.write(b"fake-lora-weights")
        return TrainingResult(
            adapter_dir=self.output_dir,
            final_train_loss=0.5,
            final_eval_loss=0.4,
            train_runtime_seconds=1.0,
            train_samples_per_second=10.0,
            steps_completed=1,
            metrics={"train_loss": 0.5},
        )


@contextmanager
def _fake_mlflow_run_scope(**kwargs):
    yield SimpleNamespace(run_id="fake-run-id", experiment_id="fake-exp-id")


def _install_worker_patches(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub):
    """Same use-site patching pattern as `test_worker_usage_events.py`."""
    import workers.tasks.training as training_module

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

    monkeypatch.setattr(training_module, "session_scope", _fake_session_scope)
    monkeypatch.setattr(training_module, "sync_redis_scope", _fake_redis_scope)
    monkeypatch.setattr(training_module, "get_minio_client", lambda: fake_minio)
    monkeypatch.setattr(training_module, "UnslothTrainer", _FakeUnslothTrainer)
    monkeypatch.setattr(training_module, "mlflow_run_scope", _fake_mlflow_run_scope)
    monkeypatch.setattr(training_module, "log_params_flat", lambda *a, **k: None)
    monkeypatch.setattr(training_module, "log_metrics_dict", lambda *a, **k: None)
    monkeypatch.setattr(training_module, "make_progress_callback", lambda **k: None)
    return training_module


def _seed_project_dataset_job(sync_sessionmaker, *, project_id, dataset_id, training_id):
    session = sync_sessionmaker()
    try:
        project = Project(id=project_id, name="proj", task_type=TaskType.QA)
        session.add(project)
        dataset = Dataset(
            id=dataset_id,
            project_id=project_id,
            name="ds",
            task_type=TaskType.QA,
            source=DatasetSource.SDG,
            status=JobStatus.COMPLETED,
            num_samples=3,
            storage_uri="s3://datasets/sdg/ds.jsonl",
        )
        session.add(dataset)
        job = TrainingJob(
            id=training_id,
            project_id=project_id,
            dataset_id=dataset_id,
            mode=TrainingMode.MANUAL,
            status=JobStatus.PENDING,
            base_model="unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
            config_json={},
        )
        session.add(job)
        session.commit()
    finally:
        session.close()


def _seed_dataset_jsonl(fake_minio, *, bucket="datasets", key="sdg/ds.jsonl"):
    from workers.storage import put_jsonl

    put_jsonl(fake_minio, bucket, key, [{"question": "q1", "answer": "a1"}])


def _adapter_objects(fake_minio, training_id) -> list[str]:
    prefix = f"adapters/{training_id}/"
    return [
        obj.object_name
        for obj in fake_minio.list_objects("models", prefix=prefix, recursive=True)
    ]


# =============================================================================
# 1. Success path — the adapter must survive
# =============================================================================


class TestSuccessPathKeepsAdapter:
    def test_success_leaves_adapter_intact(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        training_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )
        project_id, dataset_id, training_id = uuid4(), uuid4(), uuid4()
        _seed_project_dataset_job(
            sync_sessionmaker, project_id=project_id, dataset_id=dataset_id, training_id=training_id
        )
        _seed_dataset_jsonl(fake_minio)

        result = training_module.train_manual.apply(kwargs={"training_id": str(training_id)})
        assert result.successful(), f"task raised: {result.result!r}"

        objects = _adapter_objects(fake_minio, training_id)
        assert objects, "the adapter must still be present in MinIO after a successful run"

        session = sync_sessionmaker()
        try:
            job = session.get(TrainingJob, training_id)
            assert job.status == JobStatus.COMPLETED
            artifacts = session.query(ModelArtifact).filter_by(training_job_id=training_id).all()
            assert len(artifacts) == 1
            assert artifacts[0].lora_adapter_uri == f"s3://models/adapters/{training_id}"
        finally:
            session.close()


# =============================================================================
# 2. Cancel path — the adapter must be deleted
# =============================================================================


class TestCancelPathDeletesAdapter:
    def test_cancel_deletes_orphaned_adapter(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        training_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )
        project_id, dataset_id, training_id = uuid4(), uuid4(), uuid4()
        _seed_project_dataset_job(
            sync_sessionmaker, project_id=project_id, dataset_id=dataset_id, training_id=training_id
        )
        _seed_dataset_jsonl(fake_minio)

        def _cancel_before_persist(**kwargs):
            # Mirrors the cancel endpoint: flips status=CANCELLED before the
            # SIGTERM-turned-SystemExit lands, in its own transaction.
            session = sync_sessionmaker()
            try:
                job = session.get(TrainingJob, training_id)
                job.status = JobStatus.CANCELLED
                session.commit()
            finally:
                session.close()
            raise SystemExit(-241)

        monkeypatch.setattr(training_module, "_persist_artifact", _cancel_before_persist)

        try:
            training_module.train_manual.apply(kwargs={"training_id": str(training_id)})
        except SystemExit:
            pass  # `.apply()` lets BaseException through; expected.

        objects = _adapter_objects(fake_minio, training_id)
        assert objects == [], f"a cancelled run must not leave orphaned adapter objects: {objects}"

        session = sync_sessionmaker()
        try:
            job = session.get(TrainingJob, training_id)
            assert job.status == JobStatus.CANCELLED
        finally:
            session.close()


# =============================================================================
# 3. Failure path — the adapter must be deleted
# =============================================================================


class TestFailurePathDeletesAdapter:
    def test_failure_deletes_orphaned_adapter(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        training_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )
        project_id, dataset_id, training_id = uuid4(), uuid4(), uuid4()
        _seed_project_dataset_job(
            sync_sessionmaker, project_id=project_id, dataset_id=dataset_id, training_id=training_id
        )
        _seed_dataset_jsonl(fake_minio)

        def _fail_before_persist(**kwargs):
            raise RuntimeError("injected training failure for test")

        monkeypatch.setattr(training_module, "_persist_artifact", _fail_before_persist)

        result = training_module.train_manual.apply(kwargs={"training_id": str(training_id)})
        assert not result.successful()

        objects = _adapter_objects(fake_minio, training_id)
        assert objects == [], f"a failed run must not leave orphaned adapter objects: {objects}"

        session = sync_sessionmaker()
        try:
            job = session.get(TrainingJob, training_id)
            assert job.status == JobStatus.FAILED
            assert "injected training failure" in job.error_message
        finally:
            session.close()


# =============================================================================
# 4. Cleanup errors must never mask the original failure
# =============================================================================


class TestCleanupErrorDoesNotMaskOriginalFailure:
    def test_minio_error_during_cleanup_does_not_change_terminal_outcome(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        training_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )
        project_id, dataset_id, training_id = uuid4(), uuid4(), uuid4()
        _seed_project_dataset_job(
            sync_sessionmaker, project_id=project_id, dataset_id=dataset_id, training_id=training_id
        )
        _seed_dataset_jsonl(fake_minio)

        def _fail_before_persist(**kwargs):
            raise RuntimeError("injected training failure for test")

        def _broken_remove_prefix(*a, **k):
            raise ConnectionError("MinIO is down during cleanup")

        monkeypatch.setattr(training_module, "_persist_artifact", _fail_before_persist)
        monkeypatch.setattr(training_module, "remove_prefix", _broken_remove_prefix)

        result = training_module.train_manual.apply(kwargs={"training_id": str(training_id)})
        assert not result.successful()
        assert isinstance(result.result, RuntimeError)
        assert "injected training failure" in str(result.result), (
            "a cleanup error must not replace the original exception surfaced to Celery"
        )

        session = sync_sessionmaker()
        try:
            job = session.get(TrainingJob, training_id)
            assert job.status == JobStatus.FAILED
            assert "injected training failure" in job.error_message, (
                "a cleanup error must not overwrite the original failure's error_message"
            )
        finally:
            session.close()


# =============================================================================
# 5. THE test that actually exercises the `committed` guard.
#
# Every test above raises BEFORE `_persist_artifact` commits, so `committed`
# is False throughout — a broken guard (e.g. "if uploaded_prefix:" instead
# of "if not committed and uploaded_prefix:") would still pass every one of
# them, because "not deleting" and "deleting because not committed" look
# identical there. This test raises AFTER that commit, so `committed` is
# True; only a correctly gated guard leaves the adapter alone.
# =============================================================================


class TestPostCommitFailureDoesNotDeleteLiveObject:
    def test_failure_after_the_commit_must_not_delete_the_now_live_adapter(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        training_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )
        project_id, dataset_id, training_id = uuid4(), uuid4(), uuid4()
        _seed_project_dataset_job(
            sync_sessionmaker, project_id=project_id, dataset_id=dataset_id, training_id=training_id
        )
        _seed_dataset_jsonl(fake_minio)

        # Explode in the success-path log line, which runs after both
        # `_persist_artifact`'s commit (`committed = True`) AND the
        # (successful) JobCompleted publish.
        real_info = training_module.log.info

        def _explode_on_done_log(msg, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            if isinstance(msg, str) and msg.startswith("training: job=%s done"):
                raise RuntimeError("something failed after the run was committed")
            return real_info(msg, *args, **kwargs)

        monkeypatch.setattr(training_module.log, "info", _explode_on_done_log)

        try:
            training_module.train_manual.apply(kwargs={"training_id": str(training_id)})
        except RuntimeError:
            pass  # the task re-raises; expected.

        # The storage-cleanup assertion this test exists for: `committed`
        # was already True when the post-commit failure landed, so the
        # `except BaseException` handler's cleanup must have stayed
        # completely inert for this adapter.
        objects = _adapter_objects(fake_minio, training_id)
        assert objects, (
            "a post-commit failure must not delete the now-live, DB-referenced adapter"
        )

        # ...and the same `committed` flag must protect the row's terminal
        # state, not only its storage. This began as a documented gap here —
        # `training.py`'s handler wrote FAILED unconditionally, the exact bug
        # already fixed in `data_generation.py` — and was closed once the flag
        # this task introduced made the fix a one-line condition. Asserting
        # only storage survival would have left the twin defect invisible.
        with sync_sessionmaker() as session:
            job = session.get(TrainingJob, training_id)
            assert job is not None
            assert job.status == JobStatus.COMPLETED, (
                f"a durably-committed COMPLETED training run was unwound to {job.status}"
            )
            assert job.error_message is None
