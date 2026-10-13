"""Training / HPO workers record one notification per COMPLETED / FAILED job,
and none on cancel. Reuses the harnesses of the orphan-cleanup (training) and
zombie-cancel (HPO) tests."""

from __future__ import annotations

import contextlib
from uuid import uuid4

from api.models.notification import Notification
from api.models.training_job import TrainingJob
from api.schemas.enums import JobStatus
from tests.unit import test_worker_orphan_cleanup_training as tr
from tests.unit import test_worker_zombie_cancel_hpo as hpo

sync_sessionmaker = tr.sync_sessionmaker


def _set_owner(maker, training_id, owner="user-1"):
    s = maker()
    try:
        s.get(TrainingJob, training_id).owner_id = owner
        s.commit()
    finally:
        s.close()


def _notifs(maker):
    s = maker()
    try:
        return s.query(Notification).all()
    finally:
        s.close()


def _setup_training(monkeypatch, maker, fake_minio, fake_redis_pubsub):
    mod = tr._install_worker_patches(monkeypatch, maker, fake_minio, fake_redis_pubsub)
    pid, did, tid = uuid4(), uuid4(), uuid4()
    tr._seed_project_dataset_job(maker, project_id=pid, dataset_id=did, training_id=tid)
    _set_owner(maker, tid)
    tr._seed_dataset_jsonl(fake_minio)
    return mod, pid, tid


def test_training_success_notifies(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub):
    mod, pid, tid = _setup_training(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub)
    assert mod.train_manual.apply(kwargs={"training_id": str(tid)}).successful()
    (n,) = _notifs(sync_sessionmaker)
    assert (n.owner_id, n.event_type, n.resource_type, n.resource_id) == (
        "user-1", "training.completed", "training", str(tid),
    )
    assert n.payload["project_id"] == str(pid)


def test_training_failure_notifies_with_message(
    monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
):
    mod, _, tid = _setup_training(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub)

    def _boom(**kwargs):
        raise RuntimeError("injected training failure")

    monkeypatch.setattr(mod, "_persist_artifact", _boom)
    assert not mod.train_manual.apply(kwargs={"training_id": str(tid)}).successful()
    (n,) = _notifs(sync_sessionmaker)
    assert n.event_type == "training.failed"
    assert "injected training failure" in n.payload["message"]


def test_training_cancel_does_not_notify(
    monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
):
    mod, _, tid = _setup_training(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub)

    def _cancel(**kwargs):
        s = sync_sessionmaker()
        try:
            s.get(TrainingJob, tid).status = JobStatus.CANCELLED
            s.commit()
        finally:
            s.close()
        raise SystemExit(-241)

    monkeypatch.setattr(mod, "_persist_artifact", _cancel)
    with contextlib.suppress(SystemExit):
        mod.train_manual.apply(kwargs={"training_id": str(tid)})
    assert _notifs(sync_sessionmaker) == []


def test_hpo_success_notifies(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub):
    mod = hpo._install_worker_patches(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub)
    pid, did, tid = uuid4(), uuid4(), uuid4()
    hpo._seed_project_dataset_job(
        sync_sessionmaker, project_id=pid, dataset_id=did, training_id=tid, status=JobStatus.PENDING
    )
    _set_owner(sync_sessionmaker, tid)
    hpo._seed_dataset_jsonl(fake_minio)
    result = mod.train_hpo.apply(kwargs={"training_id": str(tid)})
    assert result.successful(), result.result
    (n,) = _notifs(sync_sessionmaker)
    assert (n.event_type, n.resource_id) == ("training.completed", str(tid))


def _hpo_setup(monkeypatch, maker, fake_minio, fake_redis_pubsub):
    mod = hpo._install_worker_patches(monkeypatch, maker, fake_minio, fake_redis_pubsub)
    pid, did, tid = uuid4(), uuid4(), uuid4()
    hpo._seed_project_dataset_job(
        maker, project_id=pid, dataset_id=did, training_id=tid, status=JobStatus.PENDING
    )
    _set_owner(maker, tid)
    hpo._seed_dataset_jsonl(fake_minio)
    return mod, tid


def test_hpo_failure_notifies_with_message(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub):
    mod, tid = _hpo_setup(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub)

    def _boom(bucket, key):
        raise RuntimeError("injected hpo failure")

    monkeypatch.setattr(mod, "s3_uri", _boom)
    assert not mod.train_hpo.apply(kwargs={"training_id": str(tid)}).successful()
    (n,) = _notifs(sync_sessionmaker)
    assert (n.owner_id, n.event_type, n.resource_type, n.resource_id) == (
        "user-1", "training.failed", "training", str(tid),
    )
    with sync_sessionmaker() as s:
        error_message = s.get(TrainingJob, tid).error_message
    assert "injected hpo failure" in error_message
    assert n.payload["message"] == error_message


def test_hpo_cancel_does_not_notify(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub):
    mod, tid = _hpo_setup(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub)
    real_s3_uri = mod.s3_uri

    def _cancel_mid_run(bucket, key):
        with sync_sessionmaker() as s:
            s.get(TrainingJob, tid).status = JobStatus.CANCELLED
            s.commit()
        return real_s3_uri(bucket, key)

    monkeypatch.setattr(mod, "s3_uri", _cancel_mid_run)
    result = mod.train_hpo.apply(kwargs={"training_id": str(tid)})
    assert result.result["status"] == "cancelled"
    assert _notifs(sync_sessionmaker) == []
