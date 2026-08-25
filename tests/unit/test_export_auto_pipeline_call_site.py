"""Call-site tests for the auto-pipeline sync hook inside `export_model`.

`tests/unit/test_auto_pipeline_export_resume.py` covers
`auto_pipeline.sync_export_success` and `model_export._training_id_for_artifact`
as *units* — it calls both directly and never boots the export task. That
leaves the wiring itself unproven: nothing there fails if the block inside
`export_model`'s success leg is deleted, mis-ordered (e.g. moved before the
`_persist_export_uris` commit), passed the wrong ids, or made to run on the
cancel/failure legs too.

This file closes that gap by driving the real `export_model` through the
existing MinIO/Ollama/unsloth harness in
`tests/unit/test_worker_orphan_cleanup_export.py` (helpers imported rather
than re-implemented) and asserting on what the hook actually receives.

Four properties, all of them behaviours the blocker-#11 design depends on:

  1. the success leg calls `sync_export_success` exactly once, with the
     artifact id it exported and the `TrainingJob` id resolved from it;
  2. a hook that blows up cannot fail a finished export, delete the live
     GGUF/Ollama tag, or suppress the `JobCompleted` frame — the whole point
     of the `try/except` + lazy import around the call;
  3. the discard (export cancelled between upload and commit) leg does NOT
     call it — that run produced no committed artifact to mirror;
  4. the failure leg does NOT call it either — `mark_export_failed` owns
     that path.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.models.base import Base
from api.models.model_artifact import ModelArtifact
from api.schemas.enums import JobStatus

# Harness helpers — reused verbatim so this file cannot drift from the
# established export-task test setup.
from tests.unit.test_worker_orphan_cleanup_export import (
    _export_objects,
    _install_worker_patches,
    _seed_adapter_files,
    _seed_project_job_artifact,
)


@pytest.fixture
def sync_sessionmaker():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    yield maker
    engine.dispose()


class _SyncHookSpy:
    """Records every `sync_export_success(**kwargs)` call made by the task."""

    def __init__(self, *, raises: BaseException | None = None) -> None:
        self.calls: list[dict] = []
        self._raises = raises

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return "synced"


def _install_sync_hook_spy(monkeypatch: pytest.MonkeyPatch, spy: _SyncHookSpy) -> None:
    """Patch the attribute the task actually resolves.

    `export_model` does a lazy ``from workers.tasks import auto_pipeline as
    _auto_pipeline`` and then calls ``_auto_pipeline.sync_export_success(...)``,
    so the module attribute is the right seam — patching it also proves the
    call goes through the module (a direct `from ... import sync_export_success`
    at module scope, which would re-introduce the import cycle the lazy import
    exists to avoid, would not be observable here).
    """
    import workers.tasks.auto_pipeline as auto_pipeline_module

    monkeypatch.setattr(auto_pipeline_module, "sync_export_success", spy)


def _frame_types(fake_redis_pubsub) -> list[str]:
    return [json.loads(message)["type"] for _channel, message in fake_redis_pubsub.published]


class TestSuccessLegCallsSyncHook:
    def test_success_invokes_sync_export_success_with_artifact_and_training_ids(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        ollama_calls: dict = {}
        export_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub, ollama_calls
        )
        spy = _SyncHookSpy()
        _install_sync_hook_spy(monkeypatch, spy)

        project_id, training_id, artifact_id = uuid4(), uuid4(), uuid4()
        _seed_project_job_artifact(
            sync_sessionmaker,
            project_id=project_id,
            training_id=training_id,
            artifact_id=artifact_id,
        )
        _seed_adapter_files(fake_minio, training_id=training_id)

        result = export_module.export_model.apply(
            kwargs={"artifact_id": str(artifact_id), "format": "gguf"}
        )
        assert result.successful(), f"task raised: {result.result!r}"

        assert spy.calls == [
            {"artifact_id": str(artifact_id), "training_id": str(training_id)}
        ], "the success leg must call sync_export_success exactly once, with both ids as strings"

    def test_hook_runs_after_the_export_row_is_committed(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        """Ordering matters: `sync_export_success` opens its own session and
        reads `TrainingJob.auto_pipeline`, so it must run *after*
        `_persist_export_uris` commits — otherwise the blob it mirrors would
        be synced against an artifact whose COMPLETED row is not visible yet
        (and, on a rollback, never becomes visible)."""
        ollama_calls: dict = {}
        export_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub, ollama_calls
        )
        project_id, training_id, artifact_id = uuid4(), uuid4(), uuid4()
        seen: dict = {}

        def _observe_committed_state(**kwargs):
            session = sync_sessionmaker()
            try:
                artifact = session.get(ModelArtifact, artifact_id)
                seen["export_status"] = artifact.export_status
                seen["gguf_uri"] = artifact.gguf_uri
            finally:
                session.close()
            return "synced"

        _install_sync_hook_spy(monkeypatch, _observe_committed_state)  # type: ignore[arg-type]

        _seed_project_job_artifact(
            sync_sessionmaker,
            project_id=project_id,
            training_id=training_id,
            artifact_id=artifact_id,
        )
        _seed_adapter_files(fake_minio, training_id=training_id)

        result = export_module.export_model.apply(
            kwargs={"artifact_id": str(artifact_id), "format": "gguf"}
        )
        assert result.successful(), f"task raised: {result.result!r}"

        assert seen["export_status"] == JobStatus.COMPLETED, (
            "the hook must observe the already-committed COMPLETED export row"
        )
        assert seen["gguf_uri"] == f"s3://models/exports/{artifact_id}/gguf"


class TestHookFailureCannotBreakTheExport:
    def test_raising_hook_leaves_the_export_successful_and_intact(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        ollama_calls: dict = {}
        export_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub, ollama_calls
        )
        spy = _SyncHookSpy(raises=RuntimeError("injected auto_pipeline sync failure"))
        _install_sync_hook_spy(monkeypatch, spy)

        project_id, training_id, artifact_id = uuid4(), uuid4(), uuid4()
        _seed_project_job_artifact(
            sync_sessionmaker,
            project_id=project_id,
            training_id=training_id,
            artifact_id=artifact_id,
        )
        _seed_adapter_files(fake_minio, training_id=training_id)

        result = export_module.export_model.apply(
            kwargs={"artifact_id": str(artifact_id), "format": "gguf"}
        )

        assert spy.calls, "sanity: the hook must actually have been reached"
        assert result.successful(), f"a failing sync hook must not fail the export: {result.result!r}"
        assert result.result["status"] == "completed"
        assert result.result["gguf_uri"] == f"s3://models/exports/{artifact_id}/gguf"

        # The `except BaseException` orphan-cleanup handler must stay inert:
        # this artifact is committed and live.
        assert _export_objects(fake_minio, artifact_id), (
            "a failing bookkeeping hook must not trigger orphan cleanup of a live GGUF"
        )
        assert not ollama_calls.get("deleted"), (
            "a failing bookkeeping hook must not delete the live ollama tag"
        )

        # The completion frame is published *after* the hook — a raising hook
        # must not swallow it.
        assert "completed" in _frame_types(fake_redis_pubsub), (
            f"expected a completion frame, got: {_frame_types(fake_redis_pubsub)}"
        )

        session = sync_sessionmaker()
        try:
            artifact = session.get(ModelArtifact, artifact_id)
            assert artifact.export_status == JobStatus.COMPLETED
            assert artifact.export_error_message is None, (
                "a bookkeeping-hook failure is not an export failure and must not "
                "be surfaced to the user as one"
            )
        finally:
            session.close()


class TestNonSuccessLegsDoNotCallSyncHook:
    def test_discard_path_does_not_call_the_hook(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        """Export cancelled between upload and commit: `_persist_export_uris`
        returns False, nothing was committed, so there is no successful export
        to mirror onto the pipeline blob."""
        ollama_calls: dict = {}
        export_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub, ollama_calls
        )
        spy = _SyncHookSpy()
        _install_sync_hook_spy(monkeypatch, spy)

        project_id, training_id, artifact_id = uuid4(), uuid4(), uuid4()
        _seed_project_job_artifact(
            sync_sessionmaker,
            project_id=project_id,
            training_id=training_id,
            artifact_id=artifact_id,
        )
        _seed_adapter_files(fake_minio, training_id=training_id)

        real_s3_uri = export_module.s3_uri

        def _flip_to_cancelled_then_return(bucket, key_prefix):
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
        assert result.result["status"] == "cancelled", "sanity: expected the discard path"
        assert spy.calls == [], "the discard path must not sync a non-existent success onto the blob"

    def test_failure_path_does_not_call_the_hook(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        """`mark_export_failed` (the chain's `link_error`) owns the failed
        leg's blob write; the success hook must stay out of it."""
        ollama_calls: dict = {}
        export_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub, ollama_calls
        )
        spy = _SyncHookSpy()
        _install_sync_hook_spy(monkeypatch, spy)

        project_id, training_id, artifact_id = uuid4(), uuid4(), uuid4()
        _seed_project_job_artifact(
            sync_sessionmaker,
            project_id=project_id,
            training_id=training_id,
            artifact_id=artifact_id,
        )
        _seed_adapter_files(fake_minio, training_id=training_id)

        def _fail_before_persist(**kwargs):
            raise RuntimeError("injected export failure for test")

        monkeypatch.setattr(export_module, "_persist_export_uris", _fail_before_persist)

        result = export_module.export_model.apply(
            kwargs={"artifact_id": str(artifact_id), "format": "gguf"}
        )
        assert not result.successful(), "sanity: expected the failure path"
        assert spy.calls == [], "a failed export must not mark the blob's export stage completed"
