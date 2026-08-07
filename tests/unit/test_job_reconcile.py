"""Orphan reconciliation — and, more importantly, what it must NOT touch.

A worker that dies mid-job leaves its row `running` forever with no terminal
frame, so a WebSocket-only client waits on an ending that never comes. This
sweep supplies that ending.

The dangerous failure mode is the opposite one: a sweep that is too eager
kills healthy jobs. Two guards exist for that and both are tested here —

  * **the liveness signal is the Redis snapshot, not `updated_at`.** No worker
    writes its DB row while it runs (`workers/tasks/training.py` stamps
    `mlflow_run_id` at :117 then nothing until :231), so `updated_at` is
    frozen for the whole job and a healthy two-hour training looks two hours
    stale from minute one. `TestHealthyJobsAreNeverTouched` is the test that
    would have caught that design error.
  * **`inspect()` returning nothing aborts the pass.** A broker hiccup makes
    every task id look inactive; without the abort, one blip fails every job
    on the box.

In-memory aiosqlite + fakeredis only — no broker, no Postgres, no GPU.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import fakeredis.aioredis
import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from api.core.redis_client import job_snapshot_key
from api.models.audit_event import AuditEvent
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.evaluation_run import EvaluationRun
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.enums import DatasetSource, JobStatus, TaskType, TrainingMode
from api.services import job_reconcile
from sqlalchemy import select


# ---- KNOWN GOTCHA shim: JSONB is Postgres-only, sqlite needs a stand-in ----
@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


STALE_JOB = "task-stale-0001"
LIVE_JOB = "task-live-0002"


class _Inspector:
    """Stands in for `celery_app.control.inspect()`.

    `None` for all three probes is the "broker unreachable" signal the sweep
    must treat as unknown rather than as empty.
    """

    def __init__(self, active_ids: list[str] | None = None, *, blind: bool = False):
        self._ids = active_ids or []
        self._blind = blind

    def active(self):
        if self._blind:
            return None
        return {"worker@host": [{"id": i} for i in self._ids]}

    def reserved(self):
        return None if self._blind else {}

    def scheduled(self):
        return None if self._blind else {}


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def redis():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


async def _project(db: AsyncSession) -> Project:
    p = Project(id=uuid4(), name="p", task_type=TaskType.QA, owner_id="owner-1")
    db.add(p)
    await db.flush()
    return p


async def _dataset(db: AsyncSession, project: Project, *, task_id: str, status) -> Dataset:
    d = Dataset(
        id=uuid4(),
        project_id=project.id,
        name="ds",
        task_type=TaskType.QA,
        source=DatasetSource.SDG,
        status=status,
        num_samples=0,
        celery_task_id=task_id,
    )
    db.add(d)
    await db.commit()
    return d


async def _training(db: AsyncSession, project: Project, *, task_id: str, status) -> TrainingJob:
    ds = await _dataset(db, project, task_id="seed-task", status=JobStatus.COMPLETED)
    t = TrainingJob(
        id=uuid4(),
        project_id=project.id,
        dataset_id=ds.id,
        mode=TrainingMode.MANUAL,
        status=status,
        base_model="unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
        config_json={},
        celery_task_id=task_id,
    )
    db.add(t)
    await db.commit()
    return t


async def _snapshot(redis, task_id: str, *, age: timedelta) -> None:
    """Write a progress frame as if the worker published it `age` ago."""
    ts = (datetime.now(timezone.utc) - age).isoformat()
    await redis.set(
        job_snapshot_key(task_id),
        json.dumps(
            {
                "type": "sdg_progress",
                "job_id": task_id,
                "timestamp": ts,
                "phase": "generating",
                "samples_target": 100,
            }
        ),
    )


# =============================================================================
# 1. The orphan is rescued
# =============================================================================


class TestOrphanIsReconciled:
    async def test_stale_inactive_row_is_failed_and_announced(self, db, redis) -> None:
        p = await _project(db)
        ds = await _dataset(db, p, task_id=STALE_JOB, status=JobStatus.RUNNING)
        await _snapshot(redis, STALE_JOB, age=timedelta(hours=2))

        pubsub = redis.pubsub()
        await pubsub.subscribe(f"job:{STALE_JOB}")

        report = await job_reconcile.reconcile_once(
            db, redis=redis, inspector=_Inspector([])
        )

        assert report.reconciled == [STALE_JOB]
        await db.refresh(ds)
        assert ds.status is JobStatus.FAILED
        assert "most likely died" in ds.error_message

        # The snapshot key now holds a terminal frame, so a client that
        # reconnects after the fact still learns the job ended.
        snap = json.loads(await redis.get(job_snapshot_key(STALE_JOB)))
        assert snap["type"] == "failed"
        assert snap["error_type"] == "OrphanedJob"

        # ...and a live subscriber got it too.
        msgs = []
        while (m := await pubsub.get_message(timeout=0.5)) is not None:
            if m["type"] == "message":
                msgs.append(json.loads(m["data"]))
        await pubsub.aclose()
        assert msgs and msgs[-1]["type"] == "failed"

    async def test_writes_an_audit_row(self, db, redis) -> None:
        p = await _project(db)
        await _dataset(db, p, task_id=STALE_JOB, status=JobStatus.RUNNING)
        await _snapshot(redis, STALE_JOB, age=timedelta(hours=2))

        await job_reconcile.reconcile_once(db, redis=redis, inspector=_Inspector([]))

        rows = (await db.execute(select(AuditEvent))).scalars().all()
        assert len(rows) == 1
        assert rows[0].action == "job.orphan_reconciled"
        assert rows[0].outcome == "failure"
        assert rows[0].project_id == p.id

    async def test_pending_rows_are_swept_too(self, db, redis) -> None:
        """A lost enqueue sits at `pending` forever and looks identical to a
        dead `running` job from the UI's side."""
        p = await _project(db)
        ds = await _dataset(db, p, task_id=STALE_JOB, status=JobStatus.PENDING)
        # No snapshot at all — falls back to updated_at, which is old here
        # only because we force it.
        ds.updated_at = datetime.now(timezone.utc) - timedelta(hours=3)
        await db.commit()

        report = await job_reconcile.reconcile_once(
            db, redis=redis, inspector=_Inspector([])
        )
        assert report.reconciled == [STALE_JOB]

    async def test_covers_every_job_bearing_table(self) -> None:
        """All four tables must be swept — a gap means one job type keeps the
        original bug."""
        swept = {t.model for t in job_reconcile._TARGETS}
        assert swept == {Dataset, TrainingJob, EvaluationRun, ModelArtifact}
        artifact = next(t for t in job_reconcile._TARGETS if t.model is ModelArtifact)
        # ModelArtifact differs on every axis; encode-don't-branch.
        assert artifact.status_attr == "export_status"
        assert artifact.error_attr == "export_error_message"
        assert artifact.task_id_attr == "export_celery_task_id"
        assert artifact.has_ended_at is False


# =============================================================================
# 2. The dangerous direction — healthy jobs must survive
# =============================================================================


class TestHealthyJobsAreNeverTouched:
    async def test_fresh_snapshot_protects_a_row_with_frozen_updated_at(
        self, db, redis
    ) -> None:
        """THE test. A real training writes its DB row once at the start and
        not again for hours, so `updated_at` is ancient while the job is
        perfectly healthy. Only the snapshot timestamp knows the truth —
        using `updated_at` as the primary signal would kill this job."""
        p = await _project(db)
        t = await _training(db, p, task_id=STALE_JOB, status=JobStatus.RUNNING)
        t.updated_at = datetime.now(timezone.utc) - timedelta(hours=3)
        await db.commit()
        await _snapshot(redis, STALE_JOB, age=timedelta(seconds=20))

        report = await job_reconcile.reconcile_once(
            db, redis=redis, inspector=_Inspector([])
        )

        assert report.reconciled == []
        await db.refresh(t)
        assert t.status is JobStatus.RUNNING

    async def test_active_task_is_never_touched_even_when_silent(
        self, db, redis
    ) -> None:
        """A worker holding the task is proof of life on its own — silence
        during a long model load is normal."""
        p = await _project(db)
        ds = await _dataset(db, p, task_id=LIVE_JOB, status=JobStatus.RUNNING)
        await _snapshot(redis, LIVE_JOB, age=timedelta(hours=5))

        report = await job_reconcile.reconcile_once(
            db, redis=redis, inspector=_Inspector([LIVE_JOB])
        )

        assert report.reconciled == []
        await db.refresh(ds)
        assert ds.status is JobStatus.RUNNING

    async def test_blind_inspect_aborts_the_whole_pass(self, db, redis) -> None:
        """Broker unreachable => every task id looks inactive. Treating that
        as 'nothing is running' would fail every job on the box."""
        p = await _project(db)
        ds = await _dataset(db, p, task_id=STALE_JOB, status=JobStatus.RUNNING)
        await _snapshot(redis, STALE_JOB, age=timedelta(hours=9))

        report = await job_reconcile.reconcile_once(
            db, redis=redis, inspector=_Inspector(blind=True)
        )

        assert report.aborted is True
        assert report.reconciled == []
        await db.refresh(ds)
        assert ds.status is JobStatus.RUNNING

    async def test_terminal_rows_are_left_alone(self, db, redis) -> None:
        p = await _project(db)
        ds = await _dataset(db, p, task_id=STALE_JOB, status=JobStatus.COMPLETED)
        report = await job_reconcile.reconcile_once(
            db, redis=redis, inspector=_Inspector([])
        )
        assert report.reconciled == []
        await db.refresh(ds)
        assert ds.status is JobStatus.COMPLETED


# =============================================================================
# 3. Idempotency — startup pass and the loop can overlap
# =============================================================================


class TestIdempotent:
    async def test_second_pass_is_a_no_op(self, db, redis) -> None:
        p = await _project(db)
        await _dataset(db, p, task_id=STALE_JOB, status=JobStatus.RUNNING)
        await _snapshot(redis, STALE_JOB, age=timedelta(hours=2))

        first = await job_reconcile.reconcile_once(
            db, redis=redis, inspector=_Inspector([])
        )
        second = await job_reconcile.reconcile_once(
            db, redis=redis, inspector=_Inspector([])
        )

        assert first.reconciled == [STALE_JOB]
        assert second.reconciled == []
        rows = (await db.execute(select(AuditEvent))).scalars().all()
        assert len(rows) == 1, "a second pass must not double-audit"


# =============================================================================
# 4. Source guards
# =============================================================================


class TestLoopDoesNotBlockStartup:
    """`inspect()` is a synchronous broadcast that waits out its timeout when
    the broker is unreachable. Two things follow, and both were measured
    rather than assumed — wiring this inline cost the test suite 114s per run
    instead of 6s, which is the same stall a real request would have paid.
    """

    async def test_inspect_runs_off_the_event_loop(self) -> None:
        src = (__import__("pathlib").Path(job_reconcile.__file__)).read_text(
            encoding="utf-8"
        )
        assert "await asyncio.to_thread(_active_task_ids" in src, (
            "inspect() blocks; calling it inline stalls the loop for every "
            "other request — same defect the JWKS fetch had"
        )

    async def test_inspect_timeout_is_bounded(self) -> None:
        src = (__import__("pathlib").Path(job_reconcile.__file__)).read_text(
            encoding="utf-8"
        )
        assert "control.inspect(timeout=" in src

    async def test_first_sweep_waits_for_the_initial_delay(self, monkeypatch) -> None:
        """A short-lived process must not pay for a broker round-trip it will
        never use, and a cold boot must not sweep before the workers have
        registered."""
        called = False

        async def _never(*a, **kw):
            nonlocal called
            called = True

        monkeypatch.setattr(job_reconcile, "reconcile_once", _never)
        task = __import__("asyncio").create_task(
            job_reconcile.run_forever(interval_seconds=1, initial_delay_seconds=30)
        )
        await __import__("asyncio").sleep(0.05)
        task.cancel()
        with pytest.raises(__import__("asyncio").CancelledError):
            await task
        assert called is False


def test_uses_the_async_redis_client_not_the_worker_publisher() -> None:
    """`workers/progress.py`'s publisher is sync and belongs to the worker
    process; importing it here would block the API's event loop."""
    src = (__import__("pathlib").Path(job_reconcile.__file__)).read_text(encoding="utf-8")
    assert "workers.progress" not in src
    assert "job_snapshot_key" in src and "job_channel" in src


def test_snapshot_is_written_before_publish() -> None:
    """Same ordering `workers/progress.py` documents: a client subscribing
    between the two operations must find a populated snapshot."""
    src = (__import__("pathlib").Path(job_reconcile.__file__)).read_text(encoding="utf-8")
    body = src.split("async def _publish_orphan_frame")[1]
    assert body.index("redis.set(") < body.index("redis.publish(")


# =============================================================================
# 5. Zero workers online is a FACT, not ignorance
# =============================================================================


class _App:
    """Stands in for the Celery app, for the broker-reachability probe only."""

    def __init__(self, *, reachable: bool):
        self._reachable = reachable
        self.released = 0

    def connection(self):
        return _Connection(self)


class _Connection:
    def __init__(self, app: _App):
        self._app = app

    def ensure_connection(self, **kwargs):
        if not self._app._reachable:
            raise OSError("broker unreachable")

    def release(self):
        self._app.released += 1


class TestNoWorkersOnlineIsNotIgnorance:
    """`inspect()` answers None both when the broker is down and when simply
    nobody is listening. Those call for opposite responses, and conflating
    them made the feature refuse to act in the exact case it exists for.

    This deployment runs ONE worker container at `--concurrency=1`, so "the
    worker died" and "nobody answers inspect()" are the same event. Observed
    on the vast.ai box: a SIGKILLed worker left the row in `running` while the
    sweep logged "broker or workers unreachable" every five minutes, forever.
    """

    async def test_orphan_is_reconciled_when_the_broker_is_up_but_empty(
        self, db, redis
    ) -> None:
        p = await _project(db)
        ds = await _dataset(db, p, task_id=STALE_JOB, status=JobStatus.RUNNING)
        await _snapshot(redis, STALE_JOB, age=timedelta(hours=2))

        report = await job_reconcile.reconcile_once(
            db,
            redis=redis,
            inspector=_Inspector(blind=True),
            app=_App(reachable=True),
        )

        assert report.aborted is False
        assert report.reconciled == [STALE_JOB]
        await db.refresh(ds)
        assert ds.status is JobStatus.FAILED

    async def test_unreachable_broker_still_aborts(self, db, redis) -> None:
        """The conservative branch must survive: if we cannot reach the
        broker we know nothing, and workers may be running fine behind it."""
        p = await _project(db)
        ds = await _dataset(db, p, task_id=STALE_JOB, status=JobStatus.RUNNING)
        await _snapshot(redis, STALE_JOB, age=timedelta(hours=9))

        report = await job_reconcile.reconcile_once(
            db,
            redis=redis,
            inspector=_Inspector(blind=True),
            app=_App(reachable=False),
        )

        assert report.aborted is True
        assert report.reconciled == []
        await db.refresh(ds)
        assert ds.status is JobStatus.RUNNING

    async def test_grace_period_still_protects_a_fresh_job_with_no_workers(
        self, db, redis
    ) -> None:
        """An empty fleet is not a licence to fail everything. A job that
        published a frame moments ago is inside the grace window and must
        survive — this is the case that would turn a worker restart into mass
        job failure."""
        p = await _project(db)
        ds = await _dataset(db, p, task_id=LIVE_JOB, status=JobStatus.RUNNING)
        await _snapshot(redis, LIVE_JOB, age=timedelta(seconds=5))

        report = await job_reconcile.reconcile_once(
            db,
            redis=redis,
            inspector=_Inspector(blind=True),
            app=_App(reachable=True),
        )

        assert report.reconciled == []
        await db.refresh(ds)
        assert ds.status is JobStatus.RUNNING

    async def test_the_connection_is_always_released(self, db, redis) -> None:
        """The probe runs on every pass of a loop that never ends; leaking a
        broker connection each time would be a slow resource leak."""
        app = _App(reachable=True)
        await job_reconcile.reconcile_once(
            db, redis=redis, inspector=_Inspector(blind=True), app=app
        )
        assert app.released == 1

    async def test_an_injected_inspector_alone_stays_conservative(
        self, db, redis
    ) -> None:
        """No app injected means no way to probe the broker, so the sweep must
        not assume the fleet is empty. This is what keeps every other test in
        this file hermetic."""
        p = await _project(db)
        ds = await _dataset(db, p, task_id=STALE_JOB, status=JobStatus.RUNNING)
        await _snapshot(redis, STALE_JOB, age=timedelta(hours=9))

        report = await job_reconcile.reconcile_once(
            db, redis=redis, inspector=_Inspector(blind=True)
        )

        assert report.aborted is True
        await db.refresh(ds)
        assert ds.status is JobStatus.RUNNING
