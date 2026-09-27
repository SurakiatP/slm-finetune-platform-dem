"""Unit tests for `workers/tasks/deployment.py::preload_deployment` (T4).

Runs the task synchronously via `.apply(...)` against an in-memory sync
sqlite engine + `fakeredis`, mirroring the established pattern in
`tests/unit/test_worker_orphan_cleanup_export.py` /
`tests/unit/test_worker_usage_events.py`. `OllamaClient` is replaced with an
in-memory fake so no real Ollama daemon is required.

Covered:
  1. Skip: missing row / row not PENDING -> `{"skipped": True}`, no Ollama
     call, no WS frame.
  2. Missing tag: PENDING deployment with no linked `ModelArtifact` (or an
     artifact with no `ollama_model_tag`) -> task fails, row flips to
     FAILED with an error_message, `JobFailed` published.
  3. Success: PENDING -> RUNNING, `set_keep_alive(tag, -1)` called, audit
     row written, `JobCompleted` published with the right `result` shape.
  4. Lost-the-race: the row moves off PENDING between the Ollama pin and
     the conditional UPDATE (simulated by a concurrent write inside a
     monkeypatched `session_scope`) -> best-effort unload
     (`set_keep_alive(tag, 0)`), `JobFailed` with `error_type="Cancelled"`,
     task returns normally (no exception).
  5. Ollama failure -> row flips to FAILED, `JobFailed` published, task
     raises (so Celery records the failure).
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

import workers.tasks.deployment as deployment_task
from api.models import Base, Deployment, ModelArtifact, TrainingJob
from api.schemas.enums import JobStatus, TrainingMode


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):
    return "JSON"


@pytest.fixture
def sync_sessionmaker():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    yield maker
    engine.dispose()


def _make_fake_ollama_client(calls: list, *, fail_pin: bool = False):
    """Stand-in for `workers.ollama_client.OllamaClient`. Records every
    `set_keep_alive` call in `calls`; `fail_pin` raises on the pin call
    (`keep_alive=-1`) to exercise the failure path."""

    class _Client:
        def __init__(self, base_url):
            self.base_url = base_url

        def set_keep_alive(self, tag, keep_alive):
            calls.append((tag, keep_alive))
            if fail_pin and keep_alive == -1:
                raise RuntimeError("ollama unreachable")

    return _Client


def _install_patches(monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_redis_pubsub, ollama_client_cls):
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

    monkeypatch.setattr(deployment_task, "session_scope", _fake_session_scope)
    monkeypatch.setattr(deployment_task, "sync_redis_scope", _fake_redis_scope)
    monkeypatch.setattr(deployment_task, "OllamaClient", ollama_client_cls)


def _frame_types(fake_redis_pubsub) -> list[str]:
    return [json.loads(message)["type"] for _channel, message in fake_redis_pubsub.published]


def _seed_deployment(
    sync_sessionmaker,
    *,
    deployment_id,
    owner_id="user-1",
    status=JobStatus.PENDING,
    model_artifact_id=None,
) -> None:
    session = sync_sessionmaker()
    try:
        session.add(
            Deployment(
                id=deployment_id,
                owner_id=owner_id,
                model_artifact_id=model_artifact_id,
                name="dep",
                status=status,
                rate_limit_per_min=60,
                celery_task_id=str(deployment_id),
            )
        )
        session.commit()
    finally:
        session.close()


def _seed_artifact(sync_sessionmaker, *, artifact_id, ollama_model_tag="local/my-model") -> None:
    """Minimal TrainingJob + ModelArtifact; project_id/dataset_id are left
    unresolved (sqlite in-memory enforces no FK constraints, matching the
    pattern in test_worker_orphan_cleanup_export.py)."""
    session = sync_sessionmaker()
    try:
        training_id = uuid4()
        session.add(
            TrainingJob(
                id=training_id,
                project_id=None,
                owner_id="user-1",
                dataset_id=uuid4(),
                mode=TrainingMode.MANUAL,
                status=JobStatus.COMPLETED,
                base_model="unsloth/base-4bit",
                config_json={},
            )
        )
        session.add(
            ModelArtifact(
                id=artifact_id,
                training_job_id=training_id,
                name="artifact",
                base_model="unsloth/base-4bit",
                lora_adapter_uri="s3://models/adapters/x",
                ollama_model_tag=ollama_model_tag,
            )
        )
        session.commit()
    finally:
        session.close()


# =============================================================================
# 1. Skip cases
# =============================================================================


def test_missing_row_is_skipped(monkeypatch, sync_sessionmaker, fake_redis_pubsub):
    calls: list = []
    _install_patches(monkeypatch, sync_sessionmaker, fake_redis_pubsub, _make_fake_ollama_client(calls))

    result = deployment_task.preload_deployment.apply(kwargs={"deployment_id": str(uuid4())})

    assert result.result == {"skipped": True}
    assert calls == []
    assert fake_redis_pubsub.published == []


def test_non_pending_row_is_skipped(monkeypatch, sync_sessionmaker, fake_redis_pubsub):
    calls: list = []
    _install_patches(monkeypatch, sync_sessionmaker, fake_redis_pubsub, _make_fake_ollama_client(calls))
    deployment_id = uuid4()
    _seed_deployment(sync_sessionmaker, deployment_id=deployment_id, status=JobStatus.RUNNING)

    result = deployment_task.preload_deployment.apply(kwargs={"deployment_id": str(deployment_id)})

    assert result.result == {"skipped": True}
    assert calls == []
    assert fake_redis_pubsub.published == []


# =============================================================================
# 2. Missing tag -> failure
# =============================================================================


def test_missing_tag_marks_failed_and_publishes_job_failed(monkeypatch, sync_sessionmaker, fake_redis_pubsub):
    calls: list = []
    _install_patches(monkeypatch, sync_sessionmaker, fake_redis_pubsub, _make_fake_ollama_client(calls))
    deployment_id = uuid4()
    _seed_deployment(sync_sessionmaker, deployment_id=deployment_id, model_artifact_id=None)

    result = deployment_task.preload_deployment.apply(kwargs={"deployment_id": str(deployment_id)})

    assert not result.successful()
    assert isinstance(result.result, RuntimeError)
    assert calls == []  # never reached the Ollama call
    assert _frame_types(fake_redis_pubsub) == ["failed"]

    session = sync_sessionmaker()
    try:
        row = session.get(Deployment, deployment_id)
        assert row.status == JobStatus.FAILED
        assert row.error_message
    finally:
        session.close()


# =============================================================================
# 3. Success path
# =============================================================================


def test_success_pins_model_and_flips_running(monkeypatch, sync_sessionmaker, fake_redis_pubsub):
    calls: list = []
    _install_patches(monkeypatch, sync_sessionmaker, fake_redis_pubsub, _make_fake_ollama_client(calls))
    deployment_id = uuid4()
    artifact_id = uuid4()
    _seed_artifact(sync_sessionmaker, artifact_id=artifact_id, ollama_model_tag="local/my-model")
    _seed_deployment(sync_sessionmaker, deployment_id=deployment_id, model_artifact_id=artifact_id)

    result = deployment_task.preload_deployment.apply(kwargs={"deployment_id": str(deployment_id)})

    assert result.successful(), f"task raised: {result.result!r}"
    assert result.result == {
        "status": "running",
        "deployment_id": str(deployment_id),
        "model_tag": "local/my-model",
    }
    assert calls == [("local/my-model", -1)]
    assert _frame_types(fake_redis_pubsub) == ["completed"]

    session = sync_sessionmaker()
    try:
        row = session.get(Deployment, deployment_id)
        assert row.status == JobStatus.RUNNING
    finally:
        session.close()


# =============================================================================
# 4. Lost the race: row moved off PENDING between pin and conditional UPDATE
# =============================================================================


def test_lost_pending_race_unloads_and_publishes_cancelled_job_failed(
    monkeypatch, sync_sessionmaker, fake_redis_pubsub
):
    calls: list = []
    _install_patches(monkeypatch, sync_sessionmaker, fake_redis_pubsub, _make_fake_ollama_client(calls))
    deployment_id = uuid4()
    artifact_id = uuid4()
    _seed_artifact(sync_sessionmaker, artifact_id=artifact_id, ollama_model_tag="local/my-model")
    _seed_deployment(sync_sessionmaker, deployment_id=deployment_id, model_artifact_id=artifact_id)

    call_count = {"n": 0}

    @contextmanager
    def _racing_session_scope():
        call_count["n"] += 1
        # The 2nd `session_scope()` call in the success path is the
        # conditional UPDATE — race a concurrent `stop()` in front of it by
        # flipping the row to CANCELLED just before that block runs.
        if call_count["n"] == 2:
            race_session = sync_sessionmaker()
            try:
                row = race_session.get(Deployment, deployment_id)
                row.status = JobStatus.CANCELLED
                race_session.commit()
            finally:
                race_session.close()
        session = sync_sessionmaker()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(deployment_task, "session_scope", _racing_session_scope)

    result = deployment_task.preload_deployment.apply(kwargs={"deployment_id": str(deployment_id)})

    assert result.successful(), f"task raised unexpectedly: {result.result!r}"
    assert result.result == {"status": "cancelled", "deployment_id": str(deployment_id)}
    # Pinned, then unloaded once the race was lost.
    assert calls == [("local/my-model", -1), ("local/my-model", 0)]
    assert _frame_types(fake_redis_pubsub) == ["failed"]

    session = sync_sessionmaker()
    try:
        row = session.get(Deployment, deployment_id)
        assert row.status == JobStatus.CANCELLED  # untouched by the task itself
    finally:
        session.close()


# =============================================================================
# 5. Ollama pin failure -> FAILED + JobFailed + task raises
# =============================================================================


def test_ollama_pin_failure_marks_failed_and_raises(monkeypatch, sync_sessionmaker, fake_redis_pubsub):
    calls: list = []
    _install_patches(
        monkeypatch, sync_sessionmaker, fake_redis_pubsub, _make_fake_ollama_client(calls, fail_pin=True)
    )
    deployment_id = uuid4()
    artifact_id = uuid4()
    _seed_artifact(sync_sessionmaker, artifact_id=artifact_id, ollama_model_tag="local/my-model")
    _seed_deployment(sync_sessionmaker, deployment_id=deployment_id, model_artifact_id=artifact_id)

    result = deployment_task.preload_deployment.apply(kwargs={"deployment_id": str(deployment_id)})

    assert not result.successful()
    assert isinstance(result.result, RuntimeError)
    assert _frame_types(fake_redis_pubsub) == ["failed"]

    session = sync_sessionmaker()
    try:
        row = session.get(Deployment, deployment_id)
        assert row.status == JobStatus.FAILED
        assert "ollama unreachable" in row.error_message
    finally:
        session.close()


# =============================================================================
# 6. SystemExit (SIGTERM from a stop's revoke) between the pin and the
#    conditional PENDING -> RUNNING UPDATE: must unload the orphaned pin,
#    publish JobFailed, and re-raise (BaseException, not Exception).
# =============================================================================


def test_systemexit_after_pin_before_running_update_unloads_and_reraises(
    monkeypatch, sync_sessionmaker, fake_redis_pubsub
):
    calls: list = []
    _install_patches(monkeypatch, sync_sessionmaker, fake_redis_pubsub, _make_fake_ollama_client(calls))
    deployment_id = uuid4()
    artifact_id = uuid4()
    _seed_artifact(sync_sessionmaker, artifact_id=artifact_id, ollama_model_tag="local/my-model")
    _seed_deployment(sync_sessionmaker, deployment_id=deployment_id, model_artifact_id=artifact_id)

    call_count = {"n": 0}

    @contextmanager
    def _session_scope_systemexit_on_running_update():
        call_count["n"] += 1
        # 1st call is the initial PENDING row read; 2nd is the conditional
        # PENDING -> RUNNING UPDATE — simulate a `stop_deployment` revoke's
        # SIGTERM (billiard -> SystemExit) landing in that exact window,
        # after the pin already succeeded.
        if call_count["n"] == 2:
            raise SystemExit(-241)
        session = sync_sessionmaker()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(deployment_task, "session_scope", _session_scope_systemexit_on_running_update)

    with pytest.raises(SystemExit):
        deployment_task.preload_deployment.apply(kwargs={"deployment_id": str(deployment_id)})

    # Pinned, then the handler's best-effort cleanup unloaded it.
    assert calls == [("local/my-model", -1), ("local/my-model", 0)]
    assert _frame_types(fake_redis_pubsub) == ["failed"]

    session = sync_sessionmaker()
    try:
        row = session.get(Deployment, deployment_id)
        assert row.status == JobStatus.FAILED
        assert row.error_message
    finally:
        session.close()
