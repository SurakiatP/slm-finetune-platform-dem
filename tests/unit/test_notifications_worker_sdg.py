"""SDG worker records `dataset.completed` / `dataset.failed` notifications,
and never on any cancel path. Harness mirrors test_worker_usage_events.py."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from ai_engine.data_gen.generator import SDGRunResult
from api.models.dataset import Dataset
from api.models.notification import Notification
from api.schemas.enums import JobStatus
from tests.unit import test_worker_usage_events as harness
from tests.unit.test_worker_usage_events import (
    _build_description_only_payload,
    _install_worker_patches,
    _seed_project_and_dataset,
)

sync_sessionmaker = harness.sync_sessionmaker  # fixture

OWNER = "user-1"


def _seed(maker, status=JobStatus.PENDING):
    project_id, dataset_id = uuid4(), uuid4()
    _seed_project_and_dataset(maker, project_id=project_id, dataset_id=dataset_id, status=status)
    s = maker()
    try:
        s.get(Dataset, dataset_id).owner_id = OWNER
        s.commit()
    finally:
        s.close()
    return project_id, dataset_id


def _notes(maker) -> list[Notification]:
    s = maker()
    try:
        return list(s.execute(select(Notification)).scalars().all())
    finally:
        s.close()


def _ok_result():
    return SDGRunResult(
        valid_rows=[{"question": "q", "answer": "a"}],
        rejected_count=0,
        duplicate_count=0,
        judge_rejected_count=0,
        judge_parse_failures=0,
        api_calls=1,
    )


def _run(dg, project_id, dataset_id):
    return dg.generate_synthetic_data.apply(
        kwargs={
            "request_payload": _build_description_only_payload(project_id),
            "dataset_id": str(dataset_id),
        }
    )


def _flip_cancelled(maker, dataset_id):
    s = maker()
    try:
        s.get(Dataset, dataset_id).status = JobStatus.CANCELLED
        s.commit()
    finally:
        s.close()


def test_success_records_completed(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub):
    dg = _install_worker_patches(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub)

    async def _gen(**kwargs):
        return _ok_result()

    monkeypatch.setattr(dg, "_run_generator", _gen)
    project_id, dataset_id = _seed(sync_sessionmaker)
    assert _run(dg, project_id, dataset_id).successful()

    (n,) = _notes(sync_sessionmaker)
    assert (n.owner_id, n.event_type, n.resource_type) == (OWNER, "dataset.completed", "dataset")
    assert n.resource_id == str(dataset_id)
    assert n.payload["name"] == "sdg-ds"
    assert n.payload["project_id"] == str(project_id)


def test_failure_records_failed(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub):
    dg = _install_worker_patches(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub)

    async def _gen(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(dg, "_run_generator", _gen)
    project_id, dataset_id = _seed(sync_sessionmaker)
    assert _run(dg, project_id, dataset_id).failed()

    (n,) = _notes(sync_sessionmaker)
    assert n.event_type == "dataset.failed"
    assert n.resource_id == str(dataset_id)
    assert n.payload["message"] == "boom"


def test_pre_cancelled_records_nothing(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub):
    dg = _install_worker_patches(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub)

    async def _gen(**kwargs):
        return _ok_result()

    monkeypatch.setattr(dg, "_run_generator", _gen)
    project_id, dataset_id = _seed(sync_sessionmaker, status=JobStatus.CANCELLED)
    _run(dg, project_id, dataset_id)
    assert _notes(sync_sessionmaker) == []


def test_mid_flight_cancel_discarded_records_nothing(
    monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
):
    dg = _install_worker_patches(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub)
    project_id, dataset_id = _seed(sync_sessionmaker)

    async def _gen(**kwargs):
        _flip_cancelled(sync_sessionmaker, dataset_id)  # cancel lands, generator still returns
        return _ok_result()

    monkeypatch.setattr(dg, "_run_generator", _gen)
    _run(dg, project_id, dataset_id)
    assert _notes(sync_sessionmaker) == []


def test_mid_flight_cancel_raising_records_nothing(
    monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
):
    dg = _install_worker_patches(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub)
    project_id, dataset_id = _seed(sync_sessionmaker)

    async def _gen(**kwargs):
        _flip_cancelled(sync_sessionmaker, dataset_id)
        raise RuntimeError("-241")

    monkeypatch.setattr(dg, "_run_generator", _gen)
    _run(dg, project_id, dataset_id)
    assert _notes(sync_sessionmaker) == []
