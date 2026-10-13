"""Notification rows written by `preload_deployment` (ready / failed / none on stop race)."""

from __future__ import annotations

from contextlib import contextmanager
from uuid import uuid4

import workers.tasks.deployment as deployment_task
from api.models import Deployment
from api.models.notification import Notification
from api.schemas.enums import JobStatus
from tests.unit import test_deployment_worker as harness
from tests.unit.test_deployment_worker import (
    _install_patches,
    _make_fake_ollama_client,
    _seed_artifact,
    _seed_deployment,
)

sync_sessionmaker = harness.sync_sessionmaker  # fixture


def _notifications(maker):
    s = maker()
    try:
        return s.query(Notification).all()
    finally:
        s.close()


def _run(deployment_id):
    return deployment_task.preload_deployment.apply(kwargs={"deployment_id": str(deployment_id)})


def _setup(monkeypatch, maker, fake_redis_pubsub, *, tag="local/my-model", **client_kw):
    _install_patches(monkeypatch, maker, fake_redis_pubsub, _make_fake_ollama_client([], **client_kw))
    dep_id, art_id = uuid4(), uuid4()
    _seed_artifact(maker, artifact_id=art_id, ollama_model_tag=tag)
    _seed_deployment(maker, deployment_id=dep_id, model_artifact_id=art_id)
    return dep_id


def test_success_records_ready(monkeypatch, sync_sessionmaker, fake_redis_pubsub):
    dep_id = _setup(monkeypatch, sync_sessionmaker, fake_redis_pubsub)
    assert _run(dep_id).successful()
    (n,) = _notifications(sync_sessionmaker)
    assert (n.owner_id, n.event_type, n.resource_type, n.resource_id) == (
        "user-1", "deployment.ready", "deployment", str(dep_id),
    )
    assert n.payload == {"project_id": None, "name": "dep"}


def test_pin_failure_records_failed(monkeypatch, sync_sessionmaker, fake_redis_pubsub):
    dep_id = _setup(monkeypatch, sync_sessionmaker, fake_redis_pubsub, fail_pin=True)
    assert not _run(dep_id).successful()
    (n,) = _notifications(sync_sessionmaker)
    assert n.event_type == "deployment.failed"
    assert n.payload["name"] == "dep"
    assert "ollama unreachable" in n.payload["message"]


def test_missing_tag_records_failed(monkeypatch, sync_sessionmaker, fake_redis_pubsub):
    _install_patches(monkeypatch, sync_sessionmaker, fake_redis_pubsub, _make_fake_ollama_client([]))
    dep_id = uuid4()
    _seed_deployment(sync_sessionmaker, deployment_id=dep_id, model_artifact_id=None)
    assert not _run(dep_id).successful()
    (n,) = _notifications(sync_sessionmaker)
    assert n.event_type == "deployment.failed"


def test_lost_race_records_nothing(monkeypatch, sync_sessionmaker, fake_redis_pubsub):
    dep_id = _setup(monkeypatch, sync_sessionmaker, fake_redis_pubsub)
    calls = {"n": 0}

    @contextmanager
    def racing():
        calls["n"] += 1
        s = sync_sessionmaker()
        if calls["n"] == 2:  # user stops it just before the PENDING -> RUNNING update
            s.get(Deployment, dep_id).status = JobStatus.CANCELLED
            s.commit()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    monkeypatch.setattr(deployment_task, "session_scope", racing)
    assert _run(dep_id).successful()
    assert _notifications(sync_sessionmaker) == []
