"""Unit tests for `api/services/deployments_service.py` (T3).

In-memory aiosqlite only — no Postgres, no real Ollama daemon, no real
Celery broker. `workers.tasks.deployment` (T4) is stubbed at its
`apply_async` boundary rather than imported for real behaviour, and
`OllamaClient`'s network call is stubbed via `deployments_service._unload`
so nothing here ever opens a socket.

`Dataset`/`Project`/`TrainingJob` use `sqlalchemy.dialects.postgresql.JSONB`,
which needs the usual `@compiles(JSONB, "sqlite")` shim to run against an
in-memory sqlite DB (same pattern as `test_inference_tenancy.py` /
`test_gpu_quota_guards.py`).
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from api.core.auth import CurrentUser
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.deployment import Deployment
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.deployments import DeploymentCreate, DeploymentUpdate
from api.schemas.enums import DatasetSource, JobStatus, TaskType, TrainingMode
from api.services import deployments_service, model_service


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):
    return "JSON"


USER_A = CurrentUser(id="user-a-sub", email="a@example.com")
USER_B = CurrentUser(id="user-b-sub", email="b@example.com")


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


async def _artifact_chain(
    session: AsyncSession, owner_id: str, tag: str | None
) -> ModelArtifact:
    """One project -> dataset -> training -> artifact chain, so
    `ownership.assert_model_access`'s 2-hop join has something real to
    walk (mirrors `test_inference_tenancy.py::_chain`)."""
    project = Project(id=uuid4(), name=f"p-{uuid4().hex[:6]}", task_type=TaskType.QA, owner_id=owner_id)
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
        owner_id=owner_id,
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
        name=f"m-{uuid4().hex[:6]}",
        base_model=training.base_model,
        lora_adapter_uri="s3://models/x",
        ollama_model_tag=tag,
    )
    session.add(artifact)
    await session.commit()
    return artifact


async def _existing_deployment(
    session: AsyncSession,
    *,
    owner_id: str,
    artifact_id,
    status: JobStatus = JobStatus.RUNNING,
) -> Deployment:
    deployment = Deployment(
        owner_id=owner_id,
        model_artifact_id=artifact_id,
        name="existing",
        status=status,
        rate_limit_per_min=60,
        celery_task_id=str(uuid4()),
    )
    session.add(deployment)
    await session.commit()
    return deployment


async def _race_flip_running(session: AsyncSession, deployment_id) -> None:
    """Simulate `workers/tasks/deployment.py`'s conditional PENDING -> RUNNING
    UPDATE landing between the API's read of a deployment and its own write:
    a raw core UPDATE, `synchronize_session=False` so it does NOT touch the
    already-loaded ORM object's in-memory `status` (still stale PENDING),
    exactly like a concurrent writer on a separate connection would leave it.
    """
    await session.execute(
        update(Deployment)
        .where(Deployment.id == deployment_id)
        .values(status=JobStatus.RUNNING)
        .execution_options(synchronize_session=False)
    )


@pytest.fixture
def spy_apply(monkeypatch):
    """Records every preload enqueue; the row and the job_id used as the
    Celery task_id are asserted, not assumed."""
    calls: list[dict] = []

    def _apply_async(**kwargs):
        calls.append(kwargs)

    import workers.tasks.deployment as deployment_task

    monkeypatch.setattr(deployment_task.preload_deployment, "apply_async", _apply_async)
    return calls


@pytest.fixture
def failing_apply(monkeypatch):
    def _apply_async(**kwargs):
        raise RuntimeError("broker unreachable")

    import workers.tasks.deployment as deployment_task

    monkeypatch.setattr(deployment_task.preload_deployment, "apply_async", _apply_async)


@pytest.fixture
def audit_spy(monkeypatch):
    """Captures every `audit_service.record(...)` call's kwargs while still
    performing the real `session.add()`, so DB state stays consistent."""
    calls: list[dict] = []
    original = deployments_service.audit_service.record

    def _spy(session, **kwargs):
        calls.append(kwargs)
        return original(session, **kwargs)

    monkeypatch.setattr(deployments_service.audit_service, "record", _spy)
    return calls


@pytest.fixture
def stub_unload(monkeypatch):
    calls: list[str] = []

    async def _unload(tag: str) -> None:
        calls.append(tag)

    monkeypatch.setattr(deployments_service, "_unload", _unload)
    return calls


@pytest.fixture
def spy_revoke(monkeypatch):
    calls: list[tuple] = []

    def _revoke(task_id, *, context):
        calls.append((task_id, context))

    monkeypatch.setattr(deployments_service, "revoke_celery_task", _revoke)
    return calls


# =============================================================================
# create_deployment
# =============================================================================


class TestCreateDeployment:
    async def test_success_persists_pending_and_enqueues(
        self, db, spy_apply, audit_spy
    ) -> None:
        artifact = await _artifact_chain(db, USER_A.id, "user-a-sub/my-model")

        resp = await deployments_service.create_deployment(
            db, DeploymentCreate(model_artifact_id=artifact.id), USER_A
        )

        assert resp.status is JobStatus.PENDING
        assert resp.model_tag == "user-a-sub/my-model"
        assert resp.name == artifact.name  # defaulted from the artifact
        assert resp.job_id is not None

        row = await db.get(Deployment, resp.id)
        assert row.status is JobStatus.PENDING
        assert row.celery_task_id == resp.job_id
        assert row.owner_id == USER_A.id

        assert len(spy_apply) == 1
        assert spy_apply[0]["kwargs"] == {"deployment_id": str(resp.id)}
        assert spy_apply[0]["task_id"] == resp.job_id

        assert any(c["action"] == "deployment.create" for c in audit_spy)

    async def test_explicit_name_overrides_artifact_name(self, db, spy_apply) -> None:
        artifact = await _artifact_chain(db, USER_A.id, "tag/a")
        resp = await deployments_service.create_deployment(
            db, DeploymentCreate(model_artifact_id=artifact.id, name="my-deploy"), USER_A
        )
        assert resp.name == "my-deploy"

    async def test_409_when_not_exported(self, db, spy_apply) -> None:
        artifact = await _artifact_chain(db, USER_A.id, None)
        with pytest.raises(HTTPException) as exc:
            await deployments_service.create_deployment(
                db, DeploymentCreate(model_artifact_id=artifact.id), USER_A
            )
        assert exc.value.status_code == 409
        assert spy_apply == []

    async def test_409_when_artifact_already_active(self, db, spy_apply) -> None:
        artifact = await _artifact_chain(db, USER_A.id, "tag/a")
        await _existing_deployment(
            db, owner_id=USER_B.id, artifact_id=artifact.id, status=JobStatus.PENDING
        )
        with pytest.raises(HTTPException) as exc:
            await deployments_service.create_deployment(
                db, DeploymentCreate(model_artifact_id=artifact.id), USER_A
            )
        assert exc.value.status_code == 409
        assert spy_apply == []

    async def test_429_global_cap(self, db, spy_apply) -> None:
        # Default deployment_max_active_global is 3 — fill it with three
        # other owners' deployments on three other artifacts.
        for i in range(3):
            other_artifact = await _artifact_chain(db, f"filler-{i}", f"tag/filler-{i}")
            await _existing_deployment(
                db, owner_id=f"filler-{i}", artifact_id=other_artifact.id
            )

        artifact = await _artifact_chain(db, USER_A.id, "tag/a")
        with pytest.raises(HTTPException) as exc:
            await deployments_service.create_deployment(
                db, DeploymentCreate(model_artifact_id=artifact.id), USER_A
            )
        assert exc.value.status_code == 429
        assert "Retry-After" in exc.value.headers
        assert spy_apply == []

    async def test_429_per_owner_cap(self, db, spy_apply) -> None:
        # Default deployment_max_active_per_user is 1 — USER_A already has
        # one active deployment on a different artifact.
        first_artifact = await _artifact_chain(db, USER_A.id, "tag/first")
        await _existing_deployment(db, owner_id=USER_A.id, artifact_id=first_artifact.id)

        second_artifact = await _artifact_chain(db, USER_A.id, "tag/second")
        with pytest.raises(HTTPException) as exc:
            await deployments_service.create_deployment(
                db, DeploymentCreate(model_artifact_id=second_artifact.id), USER_A
            )
        assert exc.value.status_code == 429
        assert spy_apply == []

    async def test_enqueue_failure_marks_failed_and_503(self, db, failing_apply, audit_spy) -> None:
        artifact = await _artifact_chain(db, USER_A.id, "tag/a")
        with pytest.raises(HTTPException) as exc:
            await deployments_service.create_deployment(
                db, DeploymentCreate(model_artifact_id=artifact.id), USER_A
            )
        assert exc.value.status_code == 503

        # The row survives the enqueue failure (persisted+committed before
        # the enqueue attempt) — it just moves to FAILED, not vanishes.
        stmt = select(Deployment).where(Deployment.model_artifact_id == artifact.id)
        row = (await db.execute(stmt)).scalar_one()
        assert row.status is JobStatus.FAILED
        assert row.error_message is not None
        assert any(c["action"] == "deployment.create" for c in audit_spy)

    async def test_ownership_404_for_missing_artifact(self, db) -> None:
        with pytest.raises(HTTPException) as exc:
            await deployments_service.create_deployment(
                db, DeploymentCreate(model_artifact_id=uuid4()), USER_A
            )
        assert exc.value.status_code == 404

    async def test_ownership_403_for_other_owners_artifact(self, db) -> None:
        artifact = await _artifact_chain(db, USER_B.id, "tag/b")
        with pytest.raises(HTTPException) as exc:
            await deployments_service.create_deployment(
                db, DeploymentCreate(model_artifact_id=artifact.id), USER_A
            )
        assert exc.value.status_code == 403


# =============================================================================
# list_deployments / get_deployment
# =============================================================================


class TestListDeployments:
    async def test_owner_scoped(self, db) -> None:
        artifact_a = await _artifact_chain(db, USER_A.id, "tag/a")
        artifact_b = await _artifact_chain(db, USER_B.id, "tag/b")
        await _existing_deployment(db, owner_id=USER_A.id, artifact_id=artifact_a.id)
        await _existing_deployment(db, owner_id=USER_B.id, artifact_id=artifact_b.id)

        page = await deployments_service.list_deployments(
            db, USER_A, status_filter=None, limit=50, offset=0
        )
        assert page.total == 1
        assert page.items[0].model_tag == "tag/a"

    async def test_status_filter(self, db) -> None:
        artifact = await _artifact_chain(db, USER_A.id, "tag/a")
        await _existing_deployment(
            db, owner_id=USER_A.id, artifact_id=artifact.id, status=JobStatus.COMPLETED
        )
        other_artifact = await _artifact_chain(db, USER_A.id, "tag/other")
        await _existing_deployment(
            db, owner_id=USER_A.id, artifact_id=other_artifact.id, status=JobStatus.RUNNING
        )

        page = await deployments_service.list_deployments(
            db, USER_A, status_filter=JobStatus.RUNNING, limit=50, offset=0
        )
        assert page.total == 1
        assert page.items[0].status is JobStatus.RUNNING


class TestGetDeployment:
    async def test_404_missing(self, db) -> None:
        with pytest.raises(HTTPException) as exc:
            await deployments_service.get_deployment(db, uuid4(), USER_A)
        assert exc.value.status_code == 404

    async def test_403_other_owner(self, db) -> None:
        artifact = await _artifact_chain(db, USER_B.id, "tag/b")
        deployment = await _existing_deployment(db, owner_id=USER_B.id, artifact_id=artifact.id)
        with pytest.raises(HTTPException) as exc:
            await deployments_service.get_deployment(db, deployment.id, USER_A)
        assert exc.value.status_code == 403

    async def test_success_includes_model_tag(self, db) -> None:
        artifact = await _artifact_chain(db, USER_A.id, "tag/a")
        deployment = await _existing_deployment(db, owner_id=USER_A.id, artifact_id=artifact.id)
        resp = await deployments_service.get_deployment(db, deployment.id, USER_A)
        assert resp.model_tag == "tag/a"


# =============================================================================
# update_deployment
# =============================================================================


class TestUpdateDeployment:
    async def test_422_over_rate_limit_cap(self, db) -> None:
        artifact = await _artifact_chain(db, USER_A.id, "tag/a")
        deployment = await _existing_deployment(db, owner_id=USER_A.id, artifact_id=artifact.id)
        with pytest.raises(HTTPException) as exc:
            await deployments_service.update_deployment(
                db, deployment.id, DeploymentUpdate(rate_limit_per_min=10_000), USER_A
            )
        assert exc.value.status_code == 422

    async def test_updates_name_and_rate_limit(self, db, audit_spy) -> None:
        artifact = await _artifact_chain(db, USER_A.id, "tag/a")
        deployment = await _existing_deployment(db, owner_id=USER_A.id, artifact_id=artifact.id)
        resp = await deployments_service.update_deployment(
            db, deployment.id, DeploymentUpdate(name="renamed", rate_limit_per_min=120), USER_A
        )
        assert resp.name == "renamed"
        assert resp.rate_limit_per_min == 120
        assert any(c["action"] == "deployment.update" for c in audit_spy)


# =============================================================================
# stop_deployment
# =============================================================================


class TestStopDeployment:
    async def test_pending_cancels_and_revokes(self, db, spy_revoke, audit_spy) -> None:
        artifact = await _artifact_chain(db, USER_A.id, "tag/a")
        deployment = await _existing_deployment(
            db, owner_id=USER_A.id, artifact_id=artifact.id, status=JobStatus.PENDING
        )
        resp = await deployments_service.stop_deployment(db, deployment.id, USER_A)
        assert resp.status is JobStatus.CANCELLED
        assert spy_revoke == [(deployment.celery_task_id, f"deployment {deployment.id}")]
        assert any(c["action"] == "deployment.stop" for c in audit_spy)

    async def test_running_unloads_and_completes(self, db, stub_unload, audit_spy) -> None:
        artifact = await _artifact_chain(db, USER_A.id, "tag/a")
        deployment = await _existing_deployment(
            db, owner_id=USER_A.id, artifact_id=artifact.id, status=JobStatus.RUNNING
        )
        resp = await deployments_service.stop_deployment(db, deployment.id, USER_A)
        assert resp.status is JobStatus.COMPLETED
        assert stub_unload == ["tag/a"]
        assert any(c["action"] == "deployment.stop" for c in audit_spy)

    async def test_pending_race_lost_to_worker_running_update(
        self, db, spy_revoke, stub_unload, audit_spy
    ) -> None:
        """Finding 1: the worker's own conditional PENDING -> RUNNING commits
        between the API's read and its write. The stale in-memory object is
        still PENDING, but `_apply_stop` must re-check the DB rather than
        blindly overwrite to CANCELLED (which would leak the worker's
        just-pinned model) — it should take the RUNNING branch instead.
        """
        artifact = await _artifact_chain(db, USER_A.id, "tag/a")
        deployment = await _existing_deployment(
            db, owner_id=USER_A.id, artifact_id=artifact.id, status=JobStatus.PENDING
        )
        await _race_flip_running(db, deployment.id)
        assert deployment.status is JobStatus.PENDING  # still stale in memory

        resp = await deployments_service.stop_deployment(db, deployment.id, USER_A)

        assert resp.status is JobStatus.COMPLETED
        assert stub_unload == ["tag/a"]
        assert spy_revoke == []
        assert any(c["action"] == "deployment.stop" for c in audit_spy)

    async def test_terminal_is_noop(self, db, spy_revoke, stub_unload, audit_spy) -> None:
        artifact = await _artifact_chain(db, USER_A.id, "tag/a")
        deployment = await _existing_deployment(
            db, owner_id=USER_A.id, artifact_id=artifact.id, status=JobStatus.COMPLETED
        )
        resp = await deployments_service.stop_deployment(db, deployment.id, USER_A)
        assert resp.status is JobStatus.COMPLETED
        assert spy_revoke == []
        assert stub_unload == []
        assert audit_spy == []


# =============================================================================
# delete_deployment
# =============================================================================


class TestDeleteDeployment:
    async def test_active_is_stopped_then_deleted_with_one_audit_row(
        self, db, stub_unload, audit_spy
    ) -> None:
        artifact = await _artifact_chain(db, USER_A.id, "tag/a")
        deployment = await _existing_deployment(
            db, owner_id=USER_A.id, artifact_id=artifact.id, status=JobStatus.RUNNING
        )
        await deployments_service.delete_deployment(db, deployment.id, USER_A)

        assert await db.get(Deployment, deployment.id) is None
        assert stub_unload == ["tag/a"]
        actions = [c["action"] for c in audit_spy]
        assert actions == ["deployment.delete"]  # not a separate deployment.stop

    async def test_pending_race_lost_to_worker_running_update(
        self, db, spy_revoke, stub_unload
    ) -> None:
        """Same race as `TestStopDeployment`'s, via the delete path."""
        artifact = await _artifact_chain(db, USER_A.id, "tag/a")
        deployment = await _existing_deployment(
            db, owner_id=USER_A.id, artifact_id=artifact.id, status=JobStatus.PENDING
        )
        await _race_flip_running(db, deployment.id)

        await deployments_service.delete_deployment(db, deployment.id, USER_A)

        assert await db.get(Deployment, deployment.id) is None
        assert stub_unload == ["tag/a"]
        assert spy_revoke == []

    async def test_terminal_is_just_deleted(self, db, stub_unload) -> None:
        artifact = await _artifact_chain(db, USER_A.id, "tag/a")
        deployment = await _existing_deployment(
            db, owner_id=USER_A.id, artifact_id=artifact.id, status=JobStatus.FAILED
        )
        await deployments_service.delete_deployment(db, deployment.id, USER_A)
        assert await db.get(Deployment, deployment.id) is None
        assert stub_unload == []


# =============================================================================
# stop_for_artifact (model-delete hook)
# =============================================================================


class TestStopForArtifact:
    async def test_stops_active_deployments_without_committing(
        self, db, spy_revoke, stub_unload, audit_spy
    ) -> None:
        artifact = await _artifact_chain(db, USER_A.id, "tag/a")
        pending = await _existing_deployment(
            db, owner_id=USER_A.id, artifact_id=artifact.id, status=JobStatus.PENDING
        )
        running = await _existing_deployment(
            db, owner_id=USER_B.id, artifact_id=artifact.id, status=JobStatus.RUNNING
        )
        terminal = await _existing_deployment(
            db, owner_id=USER_A.id, artifact_id=artifact.id, status=JobStatus.COMPLETED
        )

        commit_calls = []
        original_commit = db.commit

        async def _commit_spy():
            commit_calls.append(1)
            return await original_commit()

        db.commit = _commit_spy

        await deployments_service.stop_for_artifact(db, artifact)

        assert commit_calls == []  # contract: caller commits, not this function
        assert (await db.get(Deployment, pending.id)).status is JobStatus.CANCELLED
        assert (await db.get(Deployment, running.id)).status is JobStatus.COMPLETED
        assert (await db.get(Deployment, terminal.id)).status is JobStatus.COMPLETED  # untouched
        assert stub_unload == ["tag/a"]
        reasons = [c["metadata"]["reason"] for c in audit_spy]
        assert reasons == ["model_deleted", "model_deleted"]


class TestModelServicePurgeArtifactHook:
    """Wiring check: `model_service.purge_artifact` calls
    `deployments_service.stop_for_artifact` before it touches the Ollama tag
    — not re-testing `stop_for_artifact`'s own behaviour (covered above)."""

    async def test_purge_artifact_calls_stop_for_artifact(self, db, monkeypatch, fake_minio) -> None:
        artifact = await _artifact_chain(db, USER_A.id, None)  # no tag -> Ollama delete skipped
        spy = AsyncMock()
        monkeypatch.setattr(model_service.deployments_service, "stop_for_artifact", spy)

        await model_service.purge_artifact(db, artifact)

        spy.assert_awaited_once_with(db, artifact)
