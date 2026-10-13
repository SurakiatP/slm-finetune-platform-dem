"""Orphan reconcile records one `*.failed` notification for the job's owner."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from api.models.notification import Notification
from api.schemas.enums import JobStatus
from api.services import job_reconcile

# Reuse the sqlite shim, fixtures and builders of the base reconcile tests.
from tests.unit import test_job_reconcile as base
from tests.unit.test_job_reconcile import STALE_JOB, _Inspector, _project, _snapshot, _training

db = base.db
redis = base.redis


async def _notifications(db):
    return (await db.execute(select(Notification))).scalars().all()


async def _run(db, redis, owner):
    p = await _project(db)
    t = await _training(db, p, task_id=STALE_JOB, status=JobStatus.RUNNING)
    t.owner_id = owner
    await db.commit()
    await _snapshot(redis, STALE_JOB, age=timedelta(hours=2))
    await job_reconcile.reconcile_once(db, redis=redis, inspector=_Inspector([]))
    return t


async def test_orphaned_training_notifies_owner_once(db, redis) -> None:
    t = await _run(db, redis, "owner-1")
    rows = await _notifications(db)
    assert len(rows) == 1
    n = rows[0]
    assert (n.owner_id, n.event_type, n.resource_type) == ("owner-1", "training.failed", "training")
    assert n.resource_id == str(t.id)
    assert n.payload["message"] == "Job stopped responding (worker lost)."


async def test_orphan_without_owner_notifies_nobody(db, redis) -> None:
    await _run(db, redis, None)
    assert await _notifications(db) == []
