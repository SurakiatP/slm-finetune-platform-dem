"""Independent verification probes — behaviour, not source text.

Several guards shipped alongside this feature assert on source strings
("`audit_service.record(` appears in file X"). Those prove wiring exists,
not that it fires. This module drives the real code paths in-memory
(aiosqlite + fakeredis) for the cases where only a structural guard existed:

  * the orphan sweep against `EvaluationRun` and `ModelArtifact` — the two
    tables `test_job_reconcile.py` only asserts membership of `_TARGETS`
    for. `ModelArtifact` is the risky one: no `ended_at`, and its status /
    error / task-id columns are all named differently.
  * `GET /projects/{id}/activity` over HTTP, not at the service layer —
    including that the JSON key is `metadata` while the ORM attribute is
    `event_metadata`.
  * a worker task's terminal audit write actually executing against a real
    session, rather than being grepped for.
  * the request id minted by the middleware actually reaching a handler's
    log record and the `X-Request-ID` response header.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import fakeredis.aioredis
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from api.core.auth import CurrentUser
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


# ---- KNOWN GOTCHA shim: JSONB is Postgres-only, sqlite needs a stand-in ----
@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


USER_A = CurrentUser(id="user-a-sub", email="a@example.com")
USER_B = CurrentUser(id="user-b-sub", email="b@example.com")

STALE = "task-stale-verify"


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


class _Inspector:
    """No worker holds anything — the 'worker died' signal."""

    def active(self):
        return {"worker@host": []}

    def reserved(self):
        return {}

    def scheduled(self):
        return {}


async def _chain(session: AsyncSession) -> tuple[Project, TrainingJob, ModelArtifact]:
    project = Project(id=uuid4(), name="p", task_type=TaskType.QA, owner_id=USER_A.id)
    session.add(project)
    await session.flush()
    dataset = Dataset(
        id=uuid4(),
        project_id=project.id,
        name="ds",
        task_type=TaskType.QA,
        source=DatasetSource.SEED,
        status=JobStatus.COMPLETED,
        num_samples=1,
    )
    session.add(dataset)
    await session.flush()
    training = TrainingJob(
        id=uuid4(),
        project_id=project.id,
        dataset_id=dataset.id,
        mode=TrainingMode.MANUAL,
        status=JobStatus.COMPLETED,
        base_model="unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
        config_json={},
    )
    session.add(training)
    await session.flush()
    artifact = ModelArtifact(
        id=uuid4(),
        training_job_id=training.id,
        name="m",
        base_model=training.base_model,
        lora_adapter_uri="s3://models/x",
    )
    session.add(artifact)
    await session.commit()
    return project, training, artifact


async def _snapshot(redis, task_id: str, *, age: timedelta) -> None:
    await redis.set(
        job_snapshot_key(task_id),
        json.dumps(
            {
                "type": "export_progress",
                "job_id": task_id,
                "timestamp": (datetime.now(timezone.utc) - age).isoformat(),
            }
        ),
    )


# =============================================================================
# 1. The sweep on the two tables only `_TARGETS` membership covered
# =============================================================================


class TestReconcileModelArtifact:
    """`ModelArtifact` has no `ended_at` and renames every other column.
    A sweep that assumes the `Dataset` shape raises `AttributeError` here."""

    async def test_stale_export_is_failed_without_attribute_error(self, db, redis):
        project, _training, artifact = await _chain(db)
        artifact.export_status = JobStatus.RUNNING
        artifact.export_celery_task_id = STALE
        await db.commit()
        await _snapshot(redis, STALE, age=timedelta(hours=2))

        report = await job_reconcile.reconcile_once(
            db, redis=redis, inspector=_Inspector()
        )

        assert report.reconciled == [STALE]
        await db.refresh(artifact)
        assert artifact.export_status is JobStatus.FAILED
        assert "most likely died" in artifact.export_error_message
        assert not hasattr(artifact, "ended_at")

    async def test_audit_row_resolves_the_project_two_hops_up(self, db, redis):
        project, _training, artifact = await _chain(db)
        artifact.export_status = JobStatus.PENDING
        artifact.export_celery_task_id = STALE
        await db.commit()
        await _snapshot(redis, STALE, age=timedelta(hours=2))

        await job_reconcile.reconcile_once(db, redis=redis, inspector=_Inspector())

        events = (await db.execute(select(AuditEvent))).scalars().all()
        assert [e.action for e in events] == ["job.orphan_reconciled"]
        assert events[0].resource_type == "model_export"
        assert events[0].project_id == project.id

    async def test_a_terminal_frame_reaches_channel_and_snapshot(self, db, redis):
        _project, _training, artifact = await _chain(db)
        artifact.export_status = JobStatus.RUNNING
        artifact.export_celery_task_id = STALE
        await db.commit()
        await _snapshot(redis, STALE, age=timedelta(hours=2))

        pubsub = redis.pubsub()
        await pubsub.subscribe(f"job:{STALE}")
        await job_reconcile.reconcile_once(db, redis=redis, inspector=_Inspector())

        snap = json.loads(await redis.get(job_snapshot_key(STALE)))
        assert snap["type"] == "failed" and snap["error_type"] == "OrphanedJob"
        live = []
        while (m := await pubsub.get_message(timeout=0.5)) is not None:
            if m["type"] == "message":
                live.append(json.loads(m["data"]))
        await pubsub.aclose()
        assert live and live[-1]["error_type"] == "OrphanedJob"

    async def test_fresh_snapshot_protects_a_running_export(self, db, redis):
        _project, _training, artifact = await _chain(db)
        artifact.export_status = JobStatus.RUNNING
        artifact.export_celery_task_id = STALE
        await db.commit()
        # DB row is ancient; only the snapshot knows the job is alive.
        artifact.updated_at = datetime.now(timezone.utc) - timedelta(hours=6)
        await db.commit()
        await _snapshot(redis, STALE, age=timedelta(seconds=5))

        report = await job_reconcile.reconcile_once(
            db, redis=redis, inspector=_Inspector()
        )

        assert report.reconciled == []
        await db.refresh(artifact)
        assert artifact.export_status is JobStatus.RUNNING

    async def test_never_exported_artifacts_are_not_swept(self, db, redis):
        """`export_status` is NULL until someone asks for an export — such a
        row must not look like a pending job."""
        _project, _training, artifact = await _chain(db)
        assert artifact.export_status is None
        report = await job_reconcile.reconcile_once(
            db, redis=redis, inspector=_Inspector()
        )
        assert report.reconciled == []


class TestReconcileEvaluationRun:
    async def _run(self, db, *, status) -> tuple[Project, EvaluationRun]:
        project, _training, artifact = await _chain(db)
        dataset = (await db.execute(select(Dataset))).scalars().first()
        run = EvaluationRun(
            id=uuid4(),
            model_artifact_id=artifact.id,
            dataset_id=dataset.id,
            status=status,
            celery_task_id=STALE,
        )
        db.add(run)
        await db.commit()
        return project, run

    async def test_stale_run_is_failed_and_stamps_ended_at(self, db, redis):
        project, run = await self._run(db, status=JobStatus.RUNNING)
        await _snapshot(redis, STALE, age=timedelta(hours=2))

        report = await job_reconcile.reconcile_once(
            db, redis=redis, inspector=_Inspector()
        )

        assert report.reconciled == [STALE]
        await db.refresh(run)
        assert run.status is JobStatus.FAILED
        assert run.ended_at is not None
        events = (await db.execute(select(AuditEvent))).scalars().all()
        assert events[0].resource_type == "evaluation"
        # 3 hops: EvaluationRun -> ModelArtifact -> TrainingJob -> Project
        assert events[0].project_id == project.id

    async def test_fresh_snapshot_protects_a_running_evaluation(self, db, redis):
        _project, run = await self._run(db, status=JobStatus.RUNNING)
        run.updated_at = datetime.now(timezone.utc) - timedelta(hours=6)
        await db.commit()
        await _snapshot(redis, STALE, age=timedelta(seconds=5))

        report = await job_reconcile.reconcile_once(
            db, redis=redis, inspector=_Inspector()
        )

        assert report.reconciled == []
        await db.refresh(run)
        assert run.status is JobStatus.RUNNING


# =============================================================================
# 2. The activity endpoint, over HTTP
# =============================================================================


class TestActivityEndpointOverHttp:
    @pytest.fixture
    def client_and_project(self, monkeypatch):
        import asyncio

        from fastapi.testclient import TestClient

        from api.core.auth import require_user
        from api.core.database import get_db
        from api.main import app
        from api.services import audit_service

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        state: dict = {"user": USER_A}

        async def _setup():
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with maker() as session:
                project = Project(
                    id=uuid4(), name="p", task_type=TaskType.QA, owner_id=USER_A.id
                )
                session.add(project)
                await session.flush()
                base = datetime(2026, 8, 6, 12, 0, 0, tzinfo=timezone.utc)
                for offset, action in enumerate(("project.create", "sdg.submit")):
                    event = audit_service.record(
                        session,
                        action=action,
                        resource_type="project",
                        resource_id=str(project.id),
                        project_id=project.id,
                        actor_id=USER_A.id,
                        request_id="req-fixed",
                        metadata={"seq": offset},
                    )
                    event.created_at = base + timedelta(seconds=offset)
                await session.commit()
                return project.id

        project_id = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            _setup()
        )

        async def _get_db():
            async with maker() as session:
                yield session

        app.dependency_overrides[get_db] = _get_db
        app.dependency_overrides[require_user] = lambda: state["user"]
        with TestClient(app) as client:
            yield client, project_id, state
        app.dependency_overrides.clear()

    def test_owner_gets_rows_newest_first_with_metadata_key(
        self, client_and_project
    ) -> None:
        client, project_id, _state = client_and_project
        resp = client.get(f"/api/v1/projects/{project_id}/activity")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 2
        assert [i["action"] for i in body["items"]] == ["sdg.submit", "project.create"]
        # JSON key is `metadata`; `event_metadata` must not leak onto the wire.
        assert body["items"][0]["metadata"] == {"seq": 1}
        assert "event_metadata" not in body["items"][0]
        assert body["items"][0]["request_id"] == "req-fixed"

    def test_another_user_gets_403(self, client_and_project) -> None:
        """ADR-012: an existing project owned by someone else is 403, not
        the 404 this used to be."""
        client, project_id, state = client_and_project
        state["user"] = USER_B
        resp = client.get(f"/api/v1/projects/{project_id}/activity")
        assert resp.status_code == 403

    def test_missing_project_gets_404(self, client_and_project) -> None:
        """Pair for the test above: a project id that names no row at all
        must stay 404, distinct from the 403 an existing-but-not-yours
        project now gets."""
        client, _project_id, state = client_and_project
        state["user"] = USER_B
        resp = client.get(f"/api/v1/projects/{uuid4()}/activity")
        assert resp.status_code == 404

    def test_pagination_params_are_validated(self, client_and_project) -> None:
        client, project_id, _state = client_and_project
        assert client.get(
            f"/api/v1/projects/{project_id}/activity?limit=0"
        ).status_code == 422
        page = client.get(f"/api/v1/projects/{project_id}/activity?limit=1&offset=1")
        assert page.status_code == 200
        assert [i["action"] for i in page.json()["items"]] == ["project.create"]


# =============================================================================
# 3. A worker's terminal audit write, actually executed
# =============================================================================


class TestWorkerAuditWriteExecutes:
    """`test_worker_audit_events.py` only greps the source. This runs one."""

    def test_persist_artifact_writes_a_training_completed_row(self, monkeypatch):
        from contextlib import contextmanager

        from api.core import request_context
        from workers.tasks import training as training_task

        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        maker = sessionmaker(bind=engine, expire_on_commit=False)

        @contextmanager
        def _scope():
            session = maker()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        monkeypatch.setattr(training_task, "session_scope", _scope)

        with maker() as setup:
            project = Project(
                id=uuid4(), name="p", task_type=TaskType.QA, owner_id=USER_A.id
            )
            setup.add(project)
            setup.flush()
            dataset = Dataset(
                id=uuid4(),
                project_id=project.id,
                name="ds",
                task_type=TaskType.QA,
                source=DatasetSource.SEED,
                status=JobStatus.COMPLETED,
                num_samples=1,
            )
            setup.add(dataset)
            setup.flush()
            job = TrainingJob(
                id=uuid4(),
                project_id=project.id,
                dataset_id=dataset.id,
                mode=TrainingMode.MANUAL,
                status=JobStatus.RUNNING,
                base_model="unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
                config_json={},
                celery_task_id="celery-abc",
            )
            setup.add(job)
            setup.commit()
            job_uuid, project_id = job.id, project.id

        with request_context.bound(request_id="req-from-the-api"):
            artifact_id = training_task._persist_artifact(
                training_uuid=job_uuid,
                training_id=str(job_uuid),
                training_name="run-1",
                base_model="unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
                artifact_uri="s3://models/adapters/x",
                size_bytes=1024 * 1024,
            )

        with maker() as check:
            rows = check.execute(select(AuditEvent)).scalars().all()
            assert len(rows) == 1, "the worker's audit write did not execute"
            row = rows[0]
            assert row.action == "training.completed"
            assert row.outcome == "success"
            assert row.project_id == project_id
            assert row.request_id == "req-from-the-api", (
                "the API's request id did not survive into the worker's audit row"
            )
            assert row.event_metadata["model_artifact_id"] == str(artifact_id)
            assert check.get(TrainingJob, job_uuid).status is JobStatus.COMPLETED

    def test_a_failing_audit_write_rolls_back_the_completion(self, monkeypatch):
        """Same transaction, proven on the worker side: if the audit insert
        blows up, the job must not be recorded as completed either."""
        from contextlib import contextmanager

        from workers.tasks import training as training_task

        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        maker = sessionmaker(bind=engine, expire_on_commit=False)

        @contextmanager
        def _scope():
            session = maker()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        monkeypatch.setattr(training_task, "session_scope", _scope)

        def _boom(*a, **kw):
            raise RuntimeError("audit backend exploded")

        monkeypatch.setattr(training_task.audit_service, "record", _boom)

        with maker() as setup:
            project = Project(
                id=uuid4(), name="p", task_type=TaskType.QA, owner_id=USER_A.id
            )
            setup.add(project)
            setup.flush()
            dataset = Dataset(
                id=uuid4(),
                project_id=project.id,
                name="ds",
                task_type=TaskType.QA,
                source=DatasetSource.SEED,
                status=JobStatus.COMPLETED,
                num_samples=1,
            )
            setup.add(dataset)
            setup.flush()
            job = TrainingJob(
                id=uuid4(),
                project_id=project.id,
                dataset_id=dataset.id,
                mode=TrainingMode.MANUAL,
                status=JobStatus.RUNNING,
                base_model="unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
                config_json={},
            )
            setup.add(job)
            setup.commit()
            job_uuid = job.id

        with pytest.raises(RuntimeError):
            training_task._persist_artifact(
                training_uuid=job_uuid,
                training_id=str(job_uuid),
                training_name="run-1",
                base_model="unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
                artifact_uri="s3://models/adapters/x",
                size_bytes=1024,
            )

        with maker() as check:
            assert check.get(TrainingJob, job_uuid).status is JobStatus.RUNNING
            assert check.execute(select(ModelArtifact)).scalars().all() == []


# =============================================================================
# 4. Router-level dedupe for an AUTHENTICATED caller
# =============================================================================


class TestAuthenticatedRouterDedupe:
    """`test_idempotent_submit.py` drives the router anonymously and the
    authenticated case only at the `replay`/`remember` primitive. This closes
    the other half of the criterion end to end."""

    @pytest.fixture
    def app_client(self, monkeypatch):
        import fakeredis.aioredis as fr
        from fastapi.testclient import TestClient

        from api.core.auth import require_user
        from api.core.database import get_db
        from api.main import app
        from api.routers import datasets as datasets_router
        from api.schemas.sdg import SDGJobAcceptedResponse
        from api.services import idempotency

        fake = fr.FakeRedis(decode_responses=True)
        monkeypatch.setattr(idempotency, "get_redis_client", lambda: fake)

        async def _noop():
            return None

        monkeypatch.setattr(fake, "aclose", lambda: _noop())

        submitted: list[dict] = []

        async def _fake_submit(db, body):  # noqa: ANN001
            submitted.append(body.model_dump(mode="json"))
            return SDGJobAcceptedResponse(
                job_id=f"celery-task-{len(submitted)}",
                dataset_id=uuid4(),
                websocket_url="/ws/jobs/x",
            )

        async def _noop_ownership(db, project_id, user):  # noqa: ANN001
            return None

        monkeypatch.setattr(datasets_router, "submit_sdg_job", _fake_submit)
        monkeypatch.setattr(
            datasets_router.ownership, "assert_project_access", _noop_ownership
        )

        async def _fake_db():
            yield None

        state = {"user": USER_A}
        app.dependency_overrides[get_db] = _fake_db
        app.dependency_overrides[require_user] = lambda: state["user"]
        with TestClient(app) as client:
            yield client, submitted, state
        app.dependency_overrides.clear()

    @staticmethod
    def _body() -> dict:
        return {
            "sdg_mode": "description_only",
            "project_id": str(uuid4()),
            "task_type": "qa",
            "task_description": "Answer questions about the return policy",
            "num_samples": 20,
            "holdout_size": 0,
        }

    def test_double_click_enqueues_one_task(self, app_client):
        client, submitted, _state = app_client
        body = self._body()
        first = client.post("/api/v1/datasets/generate", json=body)
        second = client.post("/api/v1/datasets/generate", json=body)

        assert first.status_code == second.status_code == 202
        assert len(submitted) == 1, "the second click reached the service"
        assert first.json() == second.json()
        assert second.headers.get("X-Idempotent-Replay") == "true"

    def test_a_second_user_is_not_served_the_first_users_response(self, app_client):
        """The dedupe key is the actor; two people submitting the same body
        must each get their own job."""
        client, submitted, state = app_client
        body = self._body()
        client.post("/api/v1/datasets/generate", json=body)
        state["user"] = USER_B
        second = client.post("/api/v1/datasets/generate", json=body)

        assert len(submitted) == 2
        assert second.headers.get("X-Idempotent-Replay") is None


# =============================================================================
# 5. The request id, minted by the middleware and seen by a handler
# =============================================================================


class TestRequestIdReachesHandlerLogs:
    @pytest.fixture
    def captured(self):
        records: list[logging.LogRecord] = []

        class _Sink(logging.Handler):
            def emit(self, record):
                records.append(record)

        from api.core.logging_config import RequestContextFilter

        sink = _Sink()
        sink.addFilter(RequestContextFilter())
        root = logging.getLogger()
        root.addHandler(sink)
        yield records
        root.removeHandler(sink)

    def test_handler_log_and_response_header_share_one_id(self, captured):
        from fastapi.testclient import TestClient

        from api.main import app

        log = logging.getLogger("verification.probe")

        @app.get("/__verify_request_id")
        async def _probe():  # noqa: ANN202
            log.warning("inside the handler")
            return {"ok": True}

        with TestClient(app) as client:
            resp = client.get("/__verify_request_id")

        assert resp.status_code == 200
        header_id = resp.headers["X-Request-ID"]
        assert header_id
        inside = [r for r in captured if r.name == "verification.probe"]
        assert inside, "the handler's log line was never captured"
        assert getattr(inside[0], "request_id", None) == header_id

    def test_unhandled_500_correlation_id_is_the_request_id(self):
        """Driven with a client-supplied id so the two are comparable.

        The 500 is produced by Starlette's `ServerErrorMiddleware`, which sits
        OUTSIDE the request-context middleware — so `bound()` has already
        exited and the response carries no `X-Request-ID` header. The handler
        falls back to `request.state.request_id`, which is what makes the
        correlation id in the body the same id anyway.
        """
        from fastapi.testclient import TestClient

        from api.main import app

        @app.get("/__verify_boom")
        async def _boom():  # noqa: ANN202
            raise RuntimeError("kaboom")

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get(
                "/__verify_boom", headers={"X-Request-ID": "client-supplied-99"}
            )

        assert resp.status_code == 500
        assert resp.json()["extra"]["correlation_id"] == "client-supplied-99", (
            "the 500's correlation id is not the request id the client holds"
        )
