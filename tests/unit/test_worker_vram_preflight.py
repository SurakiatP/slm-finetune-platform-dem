"""T7: unit tests for `workers/vram.py` — the RTX 3060 GPU-VRAM preflight
check (`preflight_gpu_vram`) and the OOM-message helper (`friendly_oom_message`)
that the three GPU-bound Celery tasks (`training.py`, `hpo_training.py`,
`model_export.py`) call around their heavy work.

`torch` is NOT installed in this test environment (see `workers/vram.py`'s
module docstring) — every case here drives the module purely through
`_cuda_free_total_gb` (monkeypatched to fake torch's answer) or through
plain-Python exception objects, never through a real CUDA call.

Sections:
  (a) unit — `preflight_gpu_vram` raise/no-raise decision table, including the
      `gpu_preflight_enforce=False` kill switch.
  (b) message contract — both this module's own RuntimeError text and
      `friendly_oom_message`'s output must classify as `"oom"` via
      `api.services.metrics_sources.classify_error` (the substring contract
      both docstrings call out explicitly).
  (c) `friendly_oom_message` shape: class-name match, no-match, and the
      1000-char truncation of the original exception text.
  (d) integration — `workers.tasks.training.train_manual.apply(...)` with
      `preflight_gpu_vram` monkeypatched to raise: the job must land FAILED
      with an "out of memory" error_message, and MinIO must never be touched
      (preflight sits before the dataset pull). Fixture stack follows
      `_install_worker_patches` in `tests/unit/test_worker_orphan_cleanup_training.py:103-131`.
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

import workers.vram as vram_module
from ai_engine.training.unsloth_trainer import TrainingResult
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.enums import DatasetSource, JobStatus, TaskType, TrainingMode
from api.services.metrics_sources import classify_error


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


# =============================================================================
# (a) preflight_gpu_vram: raise / no-raise decision table
# =============================================================================


class TestPreflightDecisionTable:
    def test_low_free_memory_raises_out_of_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(vram_module, "_cuda_free_total_gb", lambda: (2.0, 12.0))

        with pytest.raises(RuntimeError) as excinfo:
            vram_module.preflight_gpu_vram(job_kind="training")

        assert "out of memory" in str(excinfo.value).lower()

    def test_ample_free_memory_does_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(vram_module, "_cuda_free_total_gb", lambda: (10.0, 12.0))

        vram_module.preflight_gpu_vram(job_kind="training")  # must not raise

    def test_no_cuda_visible_does_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(vram_module, "_cuda_free_total_gb", lambda: None)

        vram_module.preflight_gpu_vram(job_kind="training")  # must not raise

    def test_enforce_disabled_never_raises_even_when_low(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(vram_module, "_cuda_free_total_gb", lambda: (2.0, 12.0))
        monkeypatch.setattr(
            vram_module,
            "get_settings",
            lambda: SimpleNamespace(gpu_preflight_enforce=False, gpu_preflight_min_free_gb=8.0),
        )

        vram_module.preflight_gpu_vram(job_kind="training")  # must not raise


# =============================================================================
# (b) message contract: both message sources classify as "oom"
# =============================================================================


class TestOomMessageClassificationContract:
    def test_preflight_message_classifies_as_oom(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(vram_module, "_cuda_free_total_gb", lambda: (2.0, 12.0))

        with pytest.raises(RuntimeError) as excinfo:
            vram_module.preflight_gpu_vram(job_kind="training")

        assert classify_error(None, str(excinfo.value)) == "oom"

    def test_friendly_oom_message_classifies_as_oom(self) -> None:
        exc = RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")
        message = vram_module.friendly_oom_message(exc)

        assert message is not None
        assert classify_error(None, message) == "oom"


# =============================================================================
# (c) friendly_oom_message shape
# =============================================================================


class _OutOfMemoryError(RuntimeError):
    """Locally-defined stand-in for `torch.cuda.OutOfMemoryError` — matched by
    class *name* only (`workers/vram.py` can't import torch to `isinstance`
    check it), so any exception named `OutOfMemoryError` must qualify
    regardless of its message text."""


class TestFriendlyOomMessageShape:
    def test_outofmemoryerror_class_name_matches_regardless_of_text(self) -> None:
        exc = _OutOfMemoryError("driver said no")

        message = vram_module.friendly_oom_message(exc)

        assert message is not None
        assert "out of memory" in message.lower()

    def test_unrelated_exception_returns_none(self) -> None:
        assert vram_module.friendly_oom_message(ValueError("boom")) is None

    def test_original_text_is_truncated_to_1000_chars(self) -> None:
        original_text = "out of memory " + ("x" * 2000)
        assert len(original_text) > 1000
        exc = RuntimeError(original_text)

        message = vram_module.friendly_oom_message(exc)

        assert message is not None
        assert message.endswith(original_text[:1000])
        assert not message.endswith(original_text), (
            "the full (>1000 char) original text must not appear verbatim"
        )


# =============================================================================
# (d) integration: train_manual FAILS with an "out of memory" error_message,
#     and MinIO is never touched — preflight sits before the dataset pull.
# =============================================================================


class _FakeUnslothTrainer:
    """Never expected to be reached in this section — preflight raises first.
    Present only so `workers.tasks.training` imports/patches cleanly."""

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


@pytest.fixture
def sync_sessionmaker():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    yield maker
    engine.dispose()


def _install_worker_patches(monkeypatch, sync_sessionmaker, fake_redis_pubsub, minio_calls):
    """Same use-site patching pattern as `test_worker_orphan_cleanup_training.py`,
    except `get_minio_client` is replaced by a call-counting stub (rather than
    the real `fake_minio` fixture) so this file can assert MinIO was never
    even reached."""
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

    def _never_call_minio():
        minio_calls.append(True)
        raise AssertionError("get_minio_client() must never be called when preflight raises")

    monkeypatch.setattr(training_module, "session_scope", _fake_session_scope)
    monkeypatch.setattr(training_module, "sync_redis_scope", _fake_redis_scope)
    monkeypatch.setattr(training_module, "get_minio_client", _never_call_minio)
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


class TestPreflightFailsJobBeforeTouchingMinio:
    def test_preflight_oom_marks_job_failed_and_never_touches_minio(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_redis_pubsub
    ) -> None:
        minio_calls: list[bool] = []
        training_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_redis_pubsub, minio_calls
        )

        def _raise_oom(*, job_kind: str) -> None:
            raise RuntimeError(f"GPU preflight check failed for {job_kind} job: out of memory")

        monkeypatch.setattr(training_module, "preflight_gpu_vram", _raise_oom)

        project_id, dataset_id, training_id = uuid4(), uuid4(), uuid4()
        _seed_project_dataset_job(
            sync_sessionmaker, project_id=project_id, dataset_id=dataset_id, training_id=training_id
        )

        result = training_module.train_manual.apply(kwargs={"training_id": str(training_id)})
        assert not result.successful()

        session = sync_sessionmaker()
        try:
            job = session.get(TrainingJob, training_id)
            assert job.status == JobStatus.FAILED
            assert "out of memory" in job.error_message.lower()
        finally:
            session.close()

        assert minio_calls == [], "MinIO must never be touched: preflight sits before the dataset pull"
