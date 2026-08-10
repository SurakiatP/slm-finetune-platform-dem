"""Unit tests for the zombie-cancel bug as it applies to
`workers/tasks/hpo_training.py`.

The bug: a job cancelled while no worker exists yet (queued, never picked
up) stays `CANCELLED` in the DB with nothing running. A worker later starts
on it anyway (Celery has no idea the row was cancelled — that's DB state,
not broker state) and, absent a guard, runs the whole HPO study + retrain to
completion and overwrites `CANCELLED` -> `COMPLETED`. The narrower variant:
the row gets cancelled *while* a worker IS running it (still no revoke landed
in time, or the window between the API's CANCELLED write and the SIGTERM
reaching this process), and the same overwrite happens at the very end when
the run persists its result.

Two guards close both windows, mirroring the shape already used by
`test_worker_orphan_cleanup_training.py` for the orphaned-artifact bug:

  1. START-CHECK (section 1, ~job load): if the row is already CANCELLED the
     instant this task loads it, skip everything — no RUNNING flip, no
     dataset load, no study — and return a `"cancelled"` dict having
     published a terminal cancelled frame.
  2. COMPLETED-GUARD (section 6b, ~persist outcome): if the row was flipped
     to CANCELLED sometime after start-check but before the terminal write,
     discard the freshly-trained adapter (delete it from MinIO, since
     nothing durable will ever reference it), skip the `ModelArtifact`
     insert / COMPLETED flip / `training.completed` audit, and return the
     same `"cancelled"` dict instead of publishing `JobCompleted`.

Runs `train_hpo` synchronously via `.apply(...)` against an in-memory sync
sqlite engine + `fake_minio` + `fake_redis_pubsub`, mirroring
`test_worker_orphan_cleanup_training.py`'s pattern. `optuna` (a deferred
import inside the task body) is injected into `sys.modules` as a minimal
fake so this stays hermetic; `UnslothTrainer` / `mlflow_run_scope` /
`HPOObjective` / `best_params_to_config` are monkeypatched at the same
coarse boundary for the same reason — this file is about what the *worker*
does with an already-cancelled row, not about HPO search correctness.
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from ai_engine.training.unsloth_trainer import TrainingResult
from api.core.redis_client import job_snapshot_key
from api.models.audit_event import AuditEvent
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.enums import DatasetSource, JobStatus, TaskType, TrainingMode
from api.schemas.training import HPOConfig, HPOFloatRange, HPOSearchSpace


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

    Writes one real file into `output_dir` so `put_directory` (the real,
    unmocked function) has something to actually walk and upload to
    `fake_minio` — that upload, and what happens to it once the row turns
    out to be cancelled, is what this file tests.
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


class _NoOpHPOObjective:
    """Stand-in for `ai_engine.hpo.optuna_objective.HPOObjective`.

    Real HPO drives `optuna.Trial.suggest_*` + a full `UnslothTrainer.train()`
    per trial; the fake `optuna.create_study().optimize()` below never calls
    this at all (n_trials worth of work is out of scope here), so this only
    needs to exist as an importable, constructible no-op.
    """

    def __init__(self, *a, **k) -> None:
        pass

    def __call__(self, trial):  # pragma: no cover — never invoked by the fake study
        return 0.0


@contextmanager
def _fake_mlflow_run_scope(**kwargs):
    yield SimpleNamespace(run_id="fake-run-id", experiment_id="fake-exp-id")


class _FakeStudy:
    def __init__(self) -> None:
        self.best_trial = SimpleNamespace(number=0)
        self.best_value = 0.5
        self.best_params: dict = {}
        self.trials: list = []

    def optimize(self, objective, n_trials=None, timeout=None, catch=None):
        return None


def _make_fake_optuna() -> SimpleNamespace:
    samplers = SimpleNamespace(
        TPESampler=lambda **k: SimpleNamespace(kind="tpe"),
        RandomSampler=lambda **k: SimpleNamespace(kind="random"),
    )
    pruners = SimpleNamespace(
        MedianPruner=lambda **k: SimpleNamespace(kind="median"),
        NopPruner=lambda **k: SimpleNamespace(kind="none"),
    )
    return SimpleNamespace(
        create_study=lambda **kw: _FakeStudy(),
        samplers=samplers,
        pruners=pruners,
    )


def _install_worker_patches(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub):
    """Same use-site patching pattern as `test_worker_orphan_cleanup_training.py`."""
    import workers.tasks.hpo_training as hpo_task

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

    monkeypatch.setattr(hpo_task, "session_scope", _fake_session_scope)
    monkeypatch.setattr(hpo_task, "sync_redis_scope", _fake_redis_scope)
    monkeypatch.setattr(hpo_task, "get_minio_client", lambda: fake_minio)
    monkeypatch.setattr(hpo_task, "UnslothTrainer", _FakeUnslothTrainer)
    monkeypatch.setattr(hpo_task, "mlflow_run_scope", _fake_mlflow_run_scope)
    monkeypatch.setattr(hpo_task, "log_params_flat", lambda *a, **k: None)
    monkeypatch.setattr(hpo_task, "log_metrics_dict", lambda *a, **k: None)
    monkeypatch.setattr(hpo_task, "make_progress_callback", lambda **k: None)
    monkeypatch.setattr(hpo_task, "best_params_to_config", lambda bp, fixed: fixed)
    monkeypatch.setattr(hpo_task, "HPOObjective", _NoOpHPOObjective)
    monkeypatch.setitem(sys.modules, "optuna", _make_fake_optuna())
    return hpo_task


def _valid_hpo_config_json() -> dict:
    return HPOConfig(
        n_trials=2,
        search_space=HPOSearchSpace(learning_rate=HPOFloatRange(low=1e-5, high=1e-3)),
    ).model_dump(mode="json")


def _seed_project_dataset_job(
    sync_sessionmaker, *, project_id, dataset_id, training_id, status: JobStatus
):
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
            mode=TrainingMode.HPO,
            status=status,
            base_model="unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
            config_json=_valid_hpo_config_json(),
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
# 1. START-CHECK — a job cancelled before any worker ever picked it up
# =============================================================================


class TestStartCheckSkipsAlreadyCancelledJob:
    def test_start_check_returns_cancelled_without_running(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        hpo_task = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )
        project_id, dataset_id, training_id = uuid4(), uuid4(), uuid4()
        _seed_project_dataset_job(
            sync_sessionmaker,
            project_id=project_id,
            dataset_id=dataset_id,
            training_id=training_id,
            status=JobStatus.CANCELLED,
        )
        _seed_dataset_jsonl(fake_minio)

        result = hpo_task.train_hpo.apply(kwargs={"training_id": str(training_id)})
        assert result.successful(), f"task raised: {result.result!r}"
        assert result.result == {"status": "cancelled", "training_id": str(training_id)}

        session = sync_sessionmaker()
        try:
            job = session.get(TrainingJob, training_id)
            assert job.status == JobStatus.CANCELLED
            assert job.started_at is None, (
                "a zombie-cancelled job must never be flipped to RUNNING"
            )
            # No dataset/training work happened, so nothing was uploaded.
            assert _adapter_objects(fake_minio, training_id) == []
            assert (
                session.query(ModelArtifact).filter_by(training_job_id=training_id).count() == 0
            )
        finally:
            session.close()

        job_id = result.id
        assert fake_redis_pubsub.published, "a terminal frame must still be published"
        channel, payload = fake_redis_pubsub.published[-1]
        assert channel == f"job:{job_id}"
        frame = _json_loads(payload)
        assert frame["type"] == "failed"
        assert frame["error_type"] == "Cancelled"

        snap = fake_redis_pubsub.client.get(job_snapshot_key(job_id))
        assert snap is not None
        snap_frame = _json_loads(snap)
        assert snap_frame["type"] == "failed"
        assert snap_frame["error_type"] == "Cancelled"


# =============================================================================
# 2. COMPLETED-GUARD — a job cancelled mid-run (after start, before persist)
# =============================================================================


class TestCompletedGuardDiscardsMidRunCancel:
    def test_cancel_during_run_discards_result_and_deletes_adapter(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        hpo_task = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )
        project_id, dataset_id, training_id = uuid4(), uuid4(), uuid4()
        _seed_project_dataset_job(
            sync_sessionmaker,
            project_id=project_id,
            dataset_id=dataset_id,
            training_id=training_id,
            status=JobStatus.PENDING,
        )
        _seed_dataset_jsonl(fake_minio)

        real_s3_uri = hpo_task.s3_uri

        def _flip_to_cancelled_without_raising(bucket, key):
            # Mirrors a cancel landing between the adapter upload
            # (`put_directory`, just before this call) and the terminal
            # persist step (section 6b) — no exception, the run just keeps
            # going with a row that is now CANCELLED underneath it.
            session = sync_sessionmaker()
            try:
                job = session.get(TrainingJob, training_id)
                job.status = JobStatus.CANCELLED
                session.commit()
            finally:
                session.close()
            return real_s3_uri(bucket, key)

        monkeypatch.setattr(hpo_task, "s3_uri", _flip_to_cancelled_without_raising)

        result = hpo_task.train_hpo.apply(kwargs={"training_id": str(training_id)})
        assert result.successful(), f"task raised: {result.result!r}"
        assert result.result == {"status": "cancelled", "training_id": str(training_id)}

        # The adapter was uploaded (training ran to completion) but must be
        # discarded once the row turned out to be cancelled.
        assert _adapter_objects(fake_minio, training_id) == [], (
            "a mid-run-cancelled job must not leave the trained adapter behind"
        )

        session = sync_sessionmaker()
        try:
            job = session.get(TrainingJob, training_id)
            assert job.status == JobStatus.CANCELLED

            assert (
                session.query(ModelArtifact).filter_by(training_job_id=training_id).count() == 0
            ), "no ModelArtifact must ever be created for a discarded run"

            audit_actions = [
                row.action
                for row in session.query(AuditEvent)
                .filter_by(resource_type="training", resource_id=str(training_id))
                .all()
            ]
            assert "training.completed" not in audit_actions, (
                "a discarded run must not record a training.completed audit event"
            )
        finally:
            session.close()

        job_id = result.id
        frames = [_json_loads(payload) for _channel, payload in fake_redis_pubsub.published]
        assert not any(f["type"] == "completed" for f in frames), (
            "a discarded run must never publish JobCompleted"
        )
        terminal = frames[-1]
        assert terminal["type"] == "failed"
        assert terminal["error_type"] == "Cancelled"

        snap = fake_redis_pubsub.client.get(job_snapshot_key(job_id))
        assert snap is not None
        snap_frame = _json_loads(snap)
        assert snap_frame["type"] == "failed"
        assert snap_frame["error_type"] == "Cancelled"


def _json_loads(payload) -> dict:
    import json

    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    return json.loads(payload)
