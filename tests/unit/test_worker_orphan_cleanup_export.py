"""Unit tests for gap-analysis item 13 ("clean up orphaned artifacts when a
job is cancelled or fails") as it applies to `workers/tasks/model_export.py`.

`export_model` uploads the produced GGUF (or SafeTensors) directory to MinIO
(`put_directory`, under `exports/{artifact_id}/{format}/...`) and, for GGUF,
registers an Ollama tag — BOTH before the `ModelArtifact` row that
references them is committed by `_persist_export_uris`. A cancel (SIGTERM ->
`SystemExit`, caught by the existing `except BaseException`) or an ordinary
failure landing in that window used to leave the export prefix orphaned in
the `models` bucket AND a dangling Ollama tag forever — the latter
unreachable via the DB-mediated tenancy filter, but still occupying real
disk (GGUFs are large).

The fix tracks every uploaded prefix in `uploaded_prefixes`, the registered
tag (if any) in `ollama_tag_registered` — both set the instant their
respective operations succeed — and a `committed` flag set the instant
`_persist_export_uris` commits. The `except BaseException` handler deletes
both via `remove_prefix` / `OllamaClient.delete_model` only when `committed`
is still False.

Runs `export_model` synchronously via `.apply(...)` against an in-memory
sync sqlite engine + `fake_minio`, mirroring the established pattern in
`tests/unit/test_worker_usage_events.py`. The real `unsloth` package (and
its GGUF conversion pipeline, which shells out to `convert_hf_to_gguf.py` +
`llama-quantize`) is not installed in this environment and isn't needed —
this file is about what the *worker* does with an already-produced GGUF
once export hands control back (or blows up), not about GGUF conversion
correctness. `from unsloth import FastLanguageModel` (a deferred import
inside `export_model` itself) is satisfied by injecting a fake module into
`sys.modules`; `_quantize_merged_to_gguf` (which drives the two subprocess
calls) is monkeypatched to just drop a dummy `.gguf` file in place — it has
its own byte-stability coverage in `tests/unit/test_snapshot_node_7.py` and
isn't what's under test here.
"""

from __future__ import annotations

import json
import os
import sys
import types
from contextlib import contextmanager
from io import BytesIO
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from api.core.redis_client import job_snapshot_key
from api.models.audit_event import AuditEvent
from api.models.base import Base
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.enums import JobStatus, TaskType, TrainingMode


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


# Deliberately NOT one of `base_model_catalog._BASE_TO_OLLAMA_TAG`'s keys —
# an unmapped base makes `get_ollama_base_tag()` return None, so
# `export_model`'s "6.5 best-effort pull of the matching base" step is
# skipped entirely. That step does a real network call
# (`pull_ollama_base_blocking`); keeping it out of scope keeps this file
# hermetic instead of relying on a fast DNS failure to keep it harmless.
BASE_MODEL = "unsloth/not-a-mapped-base-4bit"


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


class _FakeOllamaModel:
    def save_pretrained_merged(self, target_dir, tokenizer, save_method="merged_16bit"):
        os.makedirs(target_dir, exist_ok=True)


class _FakeTokenizer:
    pass


def _install_fake_unsloth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a fake `unsloth` module so `from unsloth import
    FastLanguageModel` (a deferred import inside `export_model`) succeeds
    without the real (torch-dependent) package being installed."""
    fake_module = types.ModuleType("unsloth")

    class _FakeFastLanguageModel:
        @staticmethod
        def from_pretrained(model_name, max_seq_length, dtype, load_in_4bit):
            return _FakeOllamaModel(), _FakeTokenizer()

    fake_module.FastLanguageModel = _FakeFastLanguageModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "unsloth", fake_module)


def _fake_quantize_merged_to_gguf(*, stage_dir, workdir, quant, job_id, progress_cb=None):
    """Stand-in for the real subprocess-driving conversion helper — drops a
    dummy `.gguf` file exactly where the real one would land, so
    `put_directory` has something real to upload."""
    out_dir = os.path.join(workdir, "gguf")
    os.makedirs(out_dir, exist_ok=True)
    gguf_path = os.path.join(out_dir, f"model.{quant}.gguf")
    with open(gguf_path, "wb") as fh:
        fh.write(b"fake-gguf-bytes")
    return gguf_path


def _make_fake_ollama_client(calls: dict):
    """Stand-in for `workers.ollama_client.OllamaClient`. Records every
    upload/create/delete call in `calls` so tests can assert on Ollama
    side-effects without a real daemon."""

    class _FakeOllamaClient:
        def __init__(self, base_url):
            self.base_url = base_url

        def health(self):
            return True

        def upload_blob(self, file_path):
            calls.setdefault("uploaded", []).append(file_path)
            return "sha256:" + "a" * 64

        def create_from_blob(self, *, tag, digest, parameters=None, system=None, template=None):
            calls.setdefault("created", []).append(tag)

        def delete_model(self, tag):
            calls.setdefault("deleted", []).append(tag)

    return _FakeOllamaClient


def _install_worker_patches(
    monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub, ollama_calls: dict
):
    import workers.tasks.model_export as export_module

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

    monkeypatch.setattr(export_module, "session_scope", _fake_session_scope)
    monkeypatch.setattr(export_module, "sync_redis_scope", _fake_redis_scope)
    monkeypatch.setattr(export_module, "get_minio_client", lambda: fake_minio)
    monkeypatch.setattr(export_module, "_quantize_merged_to_gguf", _fake_quantize_merged_to_gguf)
    monkeypatch.setattr(export_module, "OllamaClient", _make_fake_ollama_client(ollama_calls))
    _install_fake_unsloth(monkeypatch)
    return export_module


def _install_worker_patches_no_unsloth(
    monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub, ollama_calls: dict
):
    """Same as `_install_worker_patches` but WITHOUT injecting a fake
    `unsloth` module — used by the start-check test, which must prove that
    a job cancelled before this worker ever picked it up exits before the
    deferred ``from unsloth import FastLanguageModel`` (a module not
    installed in this test environment) is ever reached."""
    import workers.tasks.model_export as export_module

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

    monkeypatch.setattr(export_module, "session_scope", _fake_session_scope)
    monkeypatch.setattr(export_module, "sync_redis_scope", _fake_redis_scope)
    monkeypatch.setattr(export_module, "get_minio_client", lambda: fake_minio)
    monkeypatch.setattr(export_module, "_quantize_merged_to_gguf", _fake_quantize_merged_to_gguf)
    monkeypatch.setattr(export_module, "OllamaClient", _make_fake_ollama_client(ollama_calls))
    return export_module


def _frame_types(fake_redis_pubsub) -> list[str]:
    """Ordered list of `type` fields from every message published on
    `fake_redis_pubsub.published` — same shape `test_worker_usage_events.py`
    inlines per-call; pulled out here since two tests below need it."""
    return [json.loads(message)["type"] for _channel, message in fake_redis_pubsub.published]


def _seed_project_job_artifact(
    sync_sessionmaker, *, project_id, training_id, artifact_id, export_status=None
):
    session = sync_sessionmaker()
    try:
        project = Project(id=project_id, name="proj", task_type=TaskType.QA)
        session.add(project)
        job = TrainingJob(
            id=training_id,
            project_id=project_id,
            dataset_id=uuid4(),
            mode=TrainingMode.MANUAL,
            status=JobStatus.COMPLETED,
            base_model=BASE_MODEL,
            config_json={},
        )
        session.add(job)
        artifact = ModelArtifact(
            id=artifact_id,
            training_job_id=training_id,
            name="artifact",
            base_model=BASE_MODEL,
            lora_adapter_uri=f"s3://models/adapters/{training_id}",
            export_status=export_status,
        )
        session.add(artifact)
        session.commit()
    finally:
        session.close()


def _seed_adapter_files(fake_minio, *, training_id):
    fake_minio.put_object(
        "models",
        f"adapters/{training_id}/adapter_model.safetensors",
        data=BytesIO(b"fake-adapter-weights"),
        length=21,
    )


def _export_objects(fake_minio, artifact_id) -> list[str]:
    prefix = f"exports/{artifact_id}/"
    return [
        obj.object_name for obj in fake_minio.list_objects("models", prefix=prefix, recursive=True)
    ]


# =============================================================================
# 1. Success path — GGUF prefix survives, Ollama tag is kept
# =============================================================================


class TestSuccessPathKeepsArtifactAndTag:
    def test_success_leaves_gguf_and_ollama_tag_intact(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        ollama_calls: dict = {}
        export_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub, ollama_calls
        )
        project_id, training_id, artifact_id = uuid4(), uuid4(), uuid4()
        _seed_project_job_artifact(
            sync_sessionmaker, project_id=project_id, training_id=training_id, artifact_id=artifact_id
        )
        _seed_adapter_files(fake_minio, training_id=training_id)

        result = export_module.export_model.apply(
            kwargs={"artifact_id": str(artifact_id), "format": "gguf"}
        )
        assert result.successful(), f"task raised: {result.result!r}"

        objects = _export_objects(fake_minio, artifact_id)
        assert objects, "the exported GGUF must still be present in MinIO after a successful run"

        assert ollama_calls.get("created"), "ollama registration must have happened"
        assert not ollama_calls.get("deleted"), (
            "a successfully-exported artifact's ollama tag must NOT be deleted"
        )

        session = sync_sessionmaker()
        try:
            artifact = session.get(ModelArtifact, artifact_id)
            assert artifact.export_status == JobStatus.COMPLETED
            assert artifact.gguf_uri == f"s3://models/exports/{artifact_id}/gguf"
            assert artifact.ollama_model_tag is not None
        finally:
            session.close()


# =============================================================================
# 2. Cancel path — GGUF prefix AND ollama tag are deleted
# =============================================================================


class TestCancelPathDeletesArtifactAndTag:
    def test_cancel_after_upload_and_registration_deletes_both(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        ollama_calls: dict = {}
        export_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub, ollama_calls
        )
        project_id, training_id, artifact_id = uuid4(), uuid4(), uuid4()
        _seed_project_job_artifact(
            sync_sessionmaker, project_id=project_id, training_id=training_id, artifact_id=artifact_id
        )
        _seed_adapter_files(fake_minio, training_id=training_id)

        def _cancel_before_persist(**kwargs):
            # Mirrors the cancel endpoint: flips export_status=CANCELLED
            # before the SIGTERM-turned-SystemExit lands, in its own
            # transaction — this runs AFTER the GGUF upload and Ollama
            # registration above it in `export_model`, but BEFORE the
            # commit `_persist_export_uris` would otherwise perform.
            session = sync_sessionmaker()
            try:
                artifact = session.get(ModelArtifact, artifact_id)
                artifact.export_status = JobStatus.CANCELLED
                session.commit()
            finally:
                session.close()
            raise SystemExit(-241)

        monkeypatch.setattr(export_module, "_persist_export_uris", _cancel_before_persist)

        try:
            export_module.export_model.apply(kwargs={"artifact_id": str(artifact_id), "format": "gguf"})
        except SystemExit:
            pass  # `.apply()` lets BaseException through; expected.

        objects = _export_objects(fake_minio, artifact_id)
        assert objects == [], f"a cancelled export must not leave an orphaned GGUF prefix: {objects}"
        assert ollama_calls.get("deleted") == ollama_calls.get("created"), (
            "a cancelled export must delete every ollama tag it registered"
        )

        session = sync_sessionmaker()
        try:
            artifact = session.get(ModelArtifact, artifact_id)
            assert artifact.export_status == JobStatus.CANCELLED
        finally:
            session.close()


# =============================================================================
# 3. Failure path — GGUF prefix AND ollama tag are deleted
# =============================================================================


class TestFailurePathDeletesArtifactAndTag:
    def test_failure_after_upload_and_registration_deletes_both(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        ollama_calls: dict = {}
        export_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub, ollama_calls
        )
        project_id, training_id, artifact_id = uuid4(), uuid4(), uuid4()
        _seed_project_job_artifact(
            sync_sessionmaker, project_id=project_id, training_id=training_id, artifact_id=artifact_id
        )
        _seed_adapter_files(fake_minio, training_id=training_id)

        def _fail_before_persist(**kwargs):
            raise RuntimeError("injected export failure for test")

        monkeypatch.setattr(export_module, "_persist_export_uris", _fail_before_persist)

        result = export_module.export_model.apply(
            kwargs={"artifact_id": str(artifact_id), "format": "gguf"}
        )
        assert not result.successful()

        objects = _export_objects(fake_minio, artifact_id)
        assert objects == [], f"a failed export must not leave an orphaned GGUF prefix: {objects}"
        assert ollama_calls.get("deleted") == ollama_calls.get("created"), (
            "a failed export must delete every ollama tag it registered"
        )

        session = sync_sessionmaker()
        try:
            artifact = session.get(ModelArtifact, artifact_id)
            assert artifact.export_status == JobStatus.FAILED
            assert "injected export failure" in artifact.export_error_message
        finally:
            session.close()


# =============================================================================
# 4. Cleanup errors must never mask the original failure
# =============================================================================


class TestCleanupErrorDoesNotMaskOriginalFailure:
    def test_minio_and_ollama_errors_during_cleanup_do_not_change_terminal_outcome(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        ollama_calls: dict = {}
        export_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub, ollama_calls
        )
        project_id, training_id, artifact_id = uuid4(), uuid4(), uuid4()
        _seed_project_job_artifact(
            sync_sessionmaker, project_id=project_id, training_id=training_id, artifact_id=artifact_id
        )
        _seed_adapter_files(fake_minio, training_id=training_id)

        def _fail_before_persist(**kwargs):
            raise RuntimeError("injected export failure for test")

        def _broken_remove_prefix(*a, **k):
            raise ConnectionError("MinIO is down during cleanup")

        class _BrokenOllamaClientOnDelete:
            def __init__(self, base_url):
                pass

            def health(self):
                return True

            def upload_blob(self, file_path):
                return "sha256:" + "b" * 64

            def create_from_blob(self, *, tag, digest, parameters=None, system=None, template=None):
                ollama_calls.setdefault("created", []).append(tag)

            def delete_model(self, tag):
                raise ConnectionError("ollama daemon unreachable during cleanup")

        monkeypatch.setattr(export_module, "_persist_export_uris", _fail_before_persist)
        monkeypatch.setattr(export_module, "remove_prefix", _broken_remove_prefix)
        monkeypatch.setattr(export_module, "OllamaClient", _BrokenOllamaClientOnDelete)

        result = export_module.export_model.apply(
            kwargs={"artifact_id": str(artifact_id), "format": "gguf"}
        )
        assert not result.successful()
        assert isinstance(result.result, RuntimeError)
        assert "injected export failure" in str(result.result), (
            "cleanup errors (MinIO AND ollama) must not replace the original exception "
            "surfaced to Celery"
        )

        session = sync_sessionmaker()
        try:
            artifact = session.get(ModelArtifact, artifact_id)
            assert artifact.export_status == JobStatus.FAILED
            assert "injected export failure" in artifact.export_error_message, (
                "cleanup errors must not overwrite the original failure's export_error_message"
            )
        finally:
            session.close()


# =============================================================================
# 5. THE test that actually exercises the `committed` guard.
#
# Every test above raises BEFORE `_persist_export_uris` commits, so
# `committed` is False throughout — a broken guard (e.g.
# "if uploaded_prefixes:" instead of "if not committed and
# uploaded_prefixes:") would still pass every one of them, because "not
# deleting" and "deleting because not committed" look identical there. This
# test raises AFTER that commit, so `committed` is True; only a correctly
# gated guard leaves the GGUF prefix and Ollama tag alone.
# =============================================================================


class TestPostCommitFailureDoesNotDeleteLiveArtifactOrTag:
    def test_failure_after_the_commit_must_not_delete_the_now_live_gguf_or_tag(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        ollama_calls: dict = {}
        export_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub, ollama_calls
        )
        project_id, training_id, artifact_id = uuid4(), uuid4(), uuid4()
        _seed_project_job_artifact(
            sync_sessionmaker, project_id=project_id, training_id=training_id, artifact_id=artifact_id
        )
        _seed_adapter_files(fake_minio, training_id=training_id)

        # Explode on the terminal JobCompleted publish, which fires after
        # `_persist_export_uris`'s commit (`committed = True`) — same
        # injection point `test_worker_usage_events.py`'s
        # `TestUsageIsBilledExactlyOnce` uses for the identical reason.
        real_publish = export_module.publish_ws_message

        def _explode_on_completion(redis, job_id, message):  # noqa: ANN001
            if type(message).__name__ == "JobCompleted":
                raise RuntimeError("something failed after the run was committed")
            return real_publish(redis, job_id, message)

        monkeypatch.setattr(export_module, "publish_ws_message", _explode_on_completion)

        try:
            export_module.export_model.apply(kwargs={"artifact_id": str(artifact_id), "format": "gguf"})
        except RuntimeError:
            pass  # the task re-raises; expected.

        # The storage- and Ollama-cleanup assertions this test exists for:
        # `committed` was already True when the post-commit failure landed,
        # so the `except BaseException` handler's cleanup must have stayed
        # completely inert for both.
        objects = _export_objects(fake_minio, artifact_id)
        assert objects, (
            "a post-commit failure must not delete the now-live, DB-referenced GGUF prefix"
        )
        assert not ollama_calls.get("deleted"), (
            "a post-commit failure must not delete the now-live, DB-referenced ollama tag"
        )

        # ...and the same `committed` flag must protect the row's terminal
        # state, not only its storage. `model_export.py`'s handler wrote
        # `export_status = FAILED` unconditionally — the same defect already
        # fixed in `data_generation.py`, and the same one found in
        # `training.py` — closed once this task's flag made it a one-line
        # condition. Asserting only storage survival would have hidden it.
        with sync_sessionmaker() as session:
            art = session.get(ModelArtifact, artifact_id)
            assert art is not None
            assert art.export_status == JobStatus.COMPLETED, (
                f"a durably-committed COMPLETED export was unwound to {art.export_status}"
            )
            assert art.export_error_message is None


# =============================================================================
# 6. Zombie-cancel — START-CHECK: cancelled while queued, no worker ever ran it
#
# A job cancelled while it's still queued (no worker has picked it up yet)
# gets `export_status=CANCELLED` written by the cancel endpoint immediately —
# there's no running task to revoke. If a worker later dequeues it anyway,
# the old code would blindly flip RUNNING and re-do the whole export,
# clobbering CANCELLED back to COMPLETED. `_mark_export_running` now checks
# for that at context-load and returns False instead of flipping RUNNING,
# and `export_model` exits immediately on that — crucially, BEFORE the
# deferred `from unsloth import FastLanguageModel`, so no heavy work (model
# load, GGUF conversion, MinIO upload, Ollama registration) ever starts.
# =============================================================================


class TestStartCheckSkipsAlreadyCancelledExport:
    def test_cancelled_before_worker_picks_it_up_short_circuits_before_unsloth_import(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        ollama_calls: dict = {}
        # Deliberately the "no unsloth" installer: the real `unsloth` package
        # is not installed in this environment, so if `export_model` reached
        # `from unsloth import FastLanguageModel` this test would blow up
        # with `ModuleNotFoundError` instead of passing — that failure mode
        # IS the proof that the start-check exits early enough.
        export_module = _install_worker_patches_no_unsloth(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub, ollama_calls
        )
        project_id, training_id, artifact_id = uuid4(), uuid4(), uuid4()
        _seed_project_job_artifact(
            sync_sessionmaker,
            project_id=project_id,
            training_id=training_id,
            artifact_id=artifact_id,
            export_status=JobStatus.CANCELLED,
        )
        _seed_adapter_files(fake_minio, training_id=training_id)

        result = export_module.export_model.apply(
            kwargs={"artifact_id": str(artifact_id), "format": "gguf"}
        )
        assert result.successful(), f"task raised: {result.result!r}"
        assert result.result == {
            "status": "cancelled",
            "artifact_id": str(artifact_id),
            "format": "gguf",
        }

        session = sync_sessionmaker()
        try:
            artifact = session.get(ModelArtifact, artifact_id)
            assert artifact.export_status == JobStatus.CANCELLED, (
                "the start-check must not flip a pre-cancelled row to RUNNING/COMPLETED"
            )
        finally:
            session.close()

        objects = _export_objects(fake_minio, artifact_id)
        assert objects == [], (
            f"a start-checked cancel must never upload anything to MinIO: {objects}"
        )
        assert not ollama_calls.get("created"), (
            "a start-checked cancel must never register with Ollama"
        )

        types = _frame_types(fake_redis_pubsub)
        assert types == ["failed"], f"expected exactly one cancelled frame, got {types}"

        snapshot = fake_redis_pubsub.client.get(job_snapshot_key(result.id))
        assert snapshot is not None
        snap = json.loads(snapshot)
        assert snap["type"] == "failed"
        assert snap["error_type"] == "Cancelled"


# =============================================================================
# 7. Zombie-cancel — COMPLETED-GUARD (discard path): cancelled between the
#    GGUF upload and the terminal commit, WITHOUT an exception being raised.
#
# The existing cancel tests above (#2) all drive the cancel via a raised
# `SystemExit`, which the `except BaseException` handler catches. But the
# cancel endpoint flips `export_status=CANCELLED` in its own transaction and
# only THEN revokes the task — so there's a window where the row is already
# CANCELLED but the running task hasn't been signalled yet and keeps
# executing normally, straight into `_persist_export_uris`. That helper must
# notice CANCELLED on its own (no exception involved) and return False so the
# caller can discard the just-uploaded GGUF/Ollama tag instead of writing
# COMPLETED over a CANCELLED row.
# =============================================================================


class TestCompletedGuardDiscardsWithoutException:
    def test_cancel_flips_between_upload_and_commit_without_raising(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        ollama_calls: dict = {}
        export_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub, ollama_calls
        )
        project_id, training_id, artifact_id = uuid4(), uuid4(), uuid4()
        _seed_project_job_artifact(
            sync_sessionmaker, project_id=project_id, training_id=training_id, artifact_id=artifact_id
        )
        _seed_adapter_files(fake_minio, training_id=training_id)

        real_s3_uri = export_module.s3_uri

        def _flip_to_cancelled_then_return(bucket, key_prefix):
            # Seam: `s3_uri` is called right after `uploaded_prefixes.append`
            # for the just-uploaded GGUF prefix — i.e. exactly the window the
            # discard path exists for. Flips the row to CANCELLED (mirroring
            # the cancel endpoint's own transaction) and returns normally, no
            # exception — the discard path must be reached WITHOUT going
            # through `except BaseException`.
            session = sync_sessionmaker()
            try:
                artifact = session.get(ModelArtifact, artifact_id)
                artifact.export_status = JobStatus.CANCELLED
                session.commit()
            finally:
                session.close()
            return real_s3_uri(bucket, key_prefix)

        monkeypatch.setattr(export_module, "s3_uri", _flip_to_cancelled_then_return)

        result = export_module.export_model.apply(
            kwargs={"artifact_id": str(artifact_id), "format": "gguf"}
        )
        assert result.successful(), f"task raised: {result.result!r}"
        assert result.result == {
            "status": "cancelled",
            "artifact_id": str(artifact_id),
            "format": "gguf",
        }

        assert ollama_calls.get("created"), (
            "sanity: ollama registration must have happened before the flip"
        )
        assert ollama_calls.get("deleted") == ollama_calls.get("created"), (
            "the discard path must delete every ollama tag it registered"
        )

        objects = _export_objects(fake_minio, artifact_id)
        assert objects == [], (
            f"the discard path must not leave an orphaned GGUF prefix: {objects}"
        )

        session = sync_sessionmaker()
        try:
            artifact = session.get(ModelArtifact, artifact_id)
            assert artifact.export_status == JobStatus.CANCELLED
            assert artifact.gguf_uri is None, "discard must skip the URI write"
            assert artifact.ollama_model_tag is None, "discard must skip the tag write"
            assert artifact.export_error_message is None, (
                "discard must not touch export_error_message"
            )

            completed_events = (
                session.query(AuditEvent)
                .filter(AuditEvent.resource_id == str(artifact_id))
                .filter(AuditEvent.action == "export.completed")
                .all()
            )
            assert completed_events == [], (
                "discard must skip the export.completed audit row entirely"
            )
        finally:
            session.close()

        types = _frame_types(fake_redis_pubsub)
        assert "completed" not in types, "discard must never publish JobCompleted"
        assert types[-1] == "failed", f"expected the terminal frame to be the cancelled one: {types}"

        snapshot = fake_redis_pubsub.client.get(job_snapshot_key(result.id))
        assert snapshot is not None
        snap = json.loads(snapshot)
        assert snap["type"] == "failed"
        assert snap["error_type"] == "Cancelled"
