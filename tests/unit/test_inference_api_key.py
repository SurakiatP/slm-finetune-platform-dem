"""Key-authenticated inference (T5): `_resolve_for_key`, `_consume_rate_limit`,
`_list_deployed_models`, `inference_caller`'s dispatch, and the whole path
over real HTTP.

The JWT path's own regression guard is `tests/unit/test_inference_tenancy.py`
(untouched, must keep passing byte-for-byte). This file is only the new
key-authenticated branch added alongside it.

`api/routers/inference.py` isn't mounted with `inference_caller` in
`api/main.py` yet (a later wave does that), so the HTTP-level tests build a
local `FastAPI()` app around the router instead of importing `api.main.app`.

In-memory aiosqlite + fakeredis only — no Ollama, no Postgres, no real Redis.
"""

from __future__ import annotations

from uuid import uuid4

import fakeredis
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from api.core.auth import CurrentUser
from api.core.database import get_db
from api.models.audit_event import AuditEvent
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.deployment import Deployment
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.routers import inference as inference_router
from api.schemas.api_keys import ApiKeyCreate
from api.schemas.enums import DatasetSource, JobStatus, TaskType, TrainingMode
from api.services import api_keys_service, inference_service


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):
    return "JSON"


USER_A = CurrentUser(id="user-a-sub", email="a@example.com")
USER_B = CurrentUser(id="user-b-sub", email="b@example.com")
TAG_A = "user-a-sub/my-model"


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


async def _deploy_chain(
    session: AsyncSession,
    owner_id: str,
    tag: str,
    *,
    status: JobStatus = JobStatus.RUNNING,
    rate_limit_per_min: int = 60,
    with_artifact: bool = True,
) -> Deployment:
    """Project -> Dataset -> TrainingJob -> ModelArtifact -> Deployment,
    mirroring `test_inference_tenancy.py`'s `_chain` with a Deployment on
    top (T3's table, queried directly here per the task's instruction not
    to import `deployments_service`)."""
    project = Project(id=uuid4(), name=f"p-{owner_id}", task_type=TaskType.QA, owner_id=owner_id)
    session.add(project)
    await session.flush()
    dataset = Dataset(
        id=uuid4(), project_id=project.id, name=f"ds-{owner_id}", task_type=TaskType.QA,
        source=DatasetSource.SEED, status=JobStatus.COMPLETED, num_samples=1,
    )
    session.add(dataset)
    await session.flush()
    training = TrainingJob(
        id=uuid4(), project_id=project.id, owner_id=owner_id, dataset_id=dataset.id,
        mode=TrainingMode.MANUAL, status=JobStatus.COMPLETED,
        base_model="unsloth/Llama-3.2-1B-Instruct-bnb-4bit", config_json={},
    )
    session.add(training)
    await session.flush()
    artifact_id = None
    if with_artifact:
        artifact = ModelArtifact(
            id=uuid4(), training_job_id=training.id, name=f"m-{owner_id}",
            base_model=training.base_model, lora_adapter_uri="s3://models/x",
            ollama_model_tag=tag,
        )
        session.add(artifact)
        await session.flush()
        artifact_id = artifact.id
    deployment = Deployment(
        id=uuid4(), owner_id=owner_id, model_artifact_id=artifact_id, name=f"dep-{owner_id}",
        status=status, rate_limit_per_min=rate_limit_per_min,
    )
    session.add(deployment)
    await session.commit()
    return deployment


# =============================================================================
# 1. `_resolve_for_key` — anti-oracle 404 over Deployment ownership/status
# =============================================================================


class TestResolveForKey:
    async def test_resolves_by_deployment_id(self, db) -> None:
        deployment = await _deploy_chain(db, USER_A.id, TAG_A)
        tag, resolved = await inference_service._resolve_for_key(
            db, str(deployment.id), USER_A.id
        )
        assert tag == TAG_A
        assert resolved.id == deployment.id

    async def test_resolves_by_model_tag(self, db) -> None:
        await _deploy_chain(db, USER_A.id, TAG_A)
        tag, resolved = await inference_service._resolve_for_key(db, TAG_A, USER_A.id)
        assert tag == TAG_A
        assert resolved.owner_id == USER_A.id

    async def test_suffixed_tag_resolves_like_the_jwt_path(self, db) -> None:
        await _deploy_chain(db, USER_A.id, TAG_A)
        tag, _ = await inference_service._resolve_for_key(db, f"{TAG_A}:latest", USER_A.id)
        assert tag == TAG_A

    async def test_other_owners_deployment_id_is_404(self, db) -> None:
        deployment = await _deploy_chain(db, USER_A.id, TAG_A)
        with pytest.raises(HTTPException) as exc:
            await inference_service._resolve_for_key(db, str(deployment.id), USER_B.id)
        assert exc.value.status_code == 404

    async def test_other_owners_tag_is_404(self, db) -> None:
        await _deploy_chain(db, USER_A.id, TAG_A)
        with pytest.raises(HTTPException) as exc:
            await inference_service._resolve_for_key(db, TAG_A, USER_B.id)
        assert exc.value.status_code == 404

    async def test_non_running_deployment_is_404(self, db) -> None:
        deployment = await _deploy_chain(db, USER_A.id, TAG_A, status=JobStatus.COMPLETED)
        with pytest.raises(HTTPException) as exc:
            await inference_service._resolve_for_key(db, str(deployment.id), USER_A.id)
        assert exc.value.status_code == 404
        with pytest.raises(HTTPException):
            await inference_service._resolve_for_key(db, TAG_A, USER_A.id)

    async def test_deployment_with_no_artifact_is_404(self, db) -> None:
        deployment = await _deploy_chain(db, USER_A.id, TAG_A, with_artifact=False)
        with pytest.raises(HTTPException) as exc:
            await inference_service._resolve_for_key(db, str(deployment.id), USER_A.id)
        assert exc.value.status_code == 404

    async def test_base_model_tag_is_refused(self, db) -> None:
        """A key caller cannot address a base model directly — only their
        own RUNNING deployments, unlike the JWT path which lets base tags
        through for everyone."""
        await _deploy_chain(db, USER_A.id, TAG_A)
        with pytest.raises(HTTPException) as exc:
            await inference_service._resolve_for_key(db, "llama3.2:3b", USER_A.id)
        assert exc.value.status_code == 404

    async def test_unknown_identifier_is_the_same_404_as_wrong_owner(self, db) -> None:
        deployment = await _deploy_chain(db, USER_A.id, TAG_A)
        with pytest.raises(HTTPException) as unknown:
            await inference_service._resolve_for_key(db, str(uuid4()), USER_A.id)
        with pytest.raises(HTTPException) as wrong_owner:
            await inference_service._resolve_for_key(db, str(deployment.id), USER_B.id)
        assert unknown.value.status_code == wrong_owner.value.status_code == 404


# =============================================================================
# 2. `_consume_rate_limit` — fixed-window Redis counter, fail-open on error
# =============================================================================


@pytest.fixture
def fake_redis(monkeypatch):
    server = fakeredis.FakeServer()
    client = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    monkeypatch.setattr(inference_service, "get_redis_client", lambda: client)
    return client


class _ExplodingAsyncRedis:
    """Every call raises, simulating a hard Redis outage."""

    async def incr(self, *_a, **_kw):
        raise RedisConnectionError("simulated outage")

    async def expire(self, *_a, **_kw):
        raise RedisConnectionError("simulated outage")

    async def aclose(self) -> None:
        return None


class TestConsumeRateLimit:
    async def test_within_limit_is_a_no_op(self, db, fake_redis) -> None:
        deployment = await _deploy_chain(db, USER_A.id, TAG_A, rate_limit_per_min=5)
        for _ in range(5):
            await inference_service._consume_rate_limit(deployment)  # must not raise

    async def test_exceeding_limit_is_429_with_retry_after(self, db, fake_redis) -> None:
        deployment = await _deploy_chain(db, USER_A.id, TAG_A, rate_limit_per_min=2)
        await inference_service._consume_rate_limit(deployment)
        await inference_service._consume_rate_limit(deployment)
        with pytest.raises(HTTPException) as exc:
            await inference_service._consume_rate_limit(deployment)
        assert exc.value.status_code == 429
        assert "Retry-After" in exc.value.headers
        assert int(exc.value.headers["Retry-After"]) >= 0

    async def test_limit_is_per_deployment(self, db, fake_redis) -> None:
        dep_a = await _deploy_chain(db, USER_A.id, TAG_A, rate_limit_per_min=1)
        dep_b = await _deploy_chain(db, USER_B.id, "user-b-sub/other", rate_limit_per_min=1)
        await inference_service._consume_rate_limit(dep_a)
        await inference_service._consume_rate_limit(dep_b)  # independent counter, must not raise

    async def test_redis_error_fails_open(self, db, monkeypatch) -> None:
        deployment = await _deploy_chain(db, USER_A.id, TAG_A, rate_limit_per_min=1)
        monkeypatch.setattr(
            inference_service, "get_redis_client", lambda: _ExplodingAsyncRedis()
        )
        await inference_service._consume_rate_limit(deployment)  # must not raise


# =============================================================================
# 3. `_list_deployed_models` — DB-only, RUNNING deployments of the caller
# =============================================================================


class TestListDeployedModels:
    async def test_only_the_owners_running_deployments(self, db) -> None:
        await _deploy_chain(db, USER_A.id, TAG_A)
        await _deploy_chain(db, USER_A.id, "user-a-sub/stopped", status=JobStatus.COMPLETED)
        await _deploy_chain(db, USER_B.id, "user-b-sub/theirs")

        result = await inference_service._list_deployed_models(db, USER_A.id)
        assert [m.id for m in result.data] == [TAG_A]

    async def test_no_deployments_is_an_empty_list(self, db) -> None:
        result = await inference_service._list_deployed_models(db, USER_A.id)
        assert result.data == []


# =============================================================================
# 4. `inference_caller` dispatch — key prefix vs JWT fallthrough
# =============================================================================


class TestInferenceCallerDispatch:
    async def test_key_prefixed_token_uses_the_key_path(self, db, monkeypatch) -> None:
        created = await api_keys_service.create_key(db, ApiKeyCreate(name="k"), USER_A)
        caller = await inference_router.inference_caller(db, f"Bearer {created.key}")
        assert caller.user is not None
        assert caller.user.id == USER_A.id
        assert caller.api_key_id == str(created.id)

    async def test_missing_header_falls_through_to_jwt_path(self, db, monkeypatch) -> None:
        """No token at all: unchanged JWT-path behaviour (`require_user`
        with `auth_required=False` lets an anonymous caller through)."""
        caller = await inference_router.inference_caller(db, None)
        assert caller.user is None
        assert caller.api_key_id is None

    async def test_non_key_bearer_token_falls_through_to_jwt_path(self, db, monkeypatch) -> None:
        """A JWT (or garbage) that doesn't start with the key prefix must
        never be handed to `api_keys_service.authenticate` — it goes down
        the exact JWT path this router used before."""
        calls = []
        monkeypatch.setattr(
            inference_router.api_keys_service,
            "authenticate",
            lambda *_a, **_kw: calls.append("called"),
        )

        async def _fake_current_user_optional(_authorization):
            return None

        monkeypatch.setattr(
            inference_router, "current_user_optional", _fake_current_user_optional
        )
        caller = await inference_router.inference_caller(db, "Bearer not-a-key-token")
        assert calls == []
        assert caller.user is None
        assert caller.api_key_id is None


# =============================================================================
# 5. End-to-end over HTTP — local app, `inference_caller` as the only auth
# =============================================================================


@pytest.fixture
def app_and_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    app = FastAPI()
    app.include_router(inference_router.router, prefix="/api/v1/inference")

    async def _get_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    return app, engine, maker


@pytest.fixture
def client(app_and_engine):
    app, engine, _maker = app_and_engine
    import asyncio

    async def _setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_setup())
    with TestClient(app) as c:
        yield c


async def _seed_key_and_deployment(maker, *, rate_limit_per_min: int = 60):
    async with maker() as session:
        deployment = await _deploy_chain(
            session, USER_A.id, TAG_A, rate_limit_per_min=rate_limit_per_min
        )
        created = await api_keys_service.create_key(session, ApiKeyCreate(name="k"), USER_A)
        return created.key, deployment


class TestEndToEndHttp:
    def test_chat_completion_via_key_succeeds_and_audits(
        self, app_and_engine, client, monkeypatch, fake_redis
    ) -> None:
        _app, _engine, maker = app_and_engine
        import asyncio

        key, deployment = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            _seed_key_and_deployment(maker)
        )

        async def _fake_post_json(path, payload):
            assert payload["model"] == TAG_A
            return {
                "id": "x", "created": 0, "model": TAG_A,
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "hi"},
                     "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

        monkeypatch.setattr(inference_service, "_post_json", _fake_post_json)

        resp = client.post(
            "/api/v1/inference/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": str(deployment.id), "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["choices"][0]["message"]["content"] == "hi"

        async def _check_audit() -> None:
            async with maker() as session:
                row = (
                    await session.execute(
                        select(AuditEvent).where(AuditEvent.action == "inference.chat_completions")
                    )
                ).scalar_one()
                assert row.event_metadata["auth"] == "api_key"
                assert row.event_metadata["deployment_id"] == str(deployment.id)
                assert row.event_metadata["api_key_id"]
                assert row.outcome == "success"

        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_check_audit())

    def test_wrong_owner_deployment_is_404(
        self, app_and_engine, client, monkeypatch, fake_redis
    ) -> None:
        _app, _engine, maker = app_and_engine
        import asyncio

        key, _deployment = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            _seed_key_and_deployment(maker)
        )

        async def _seed_other() -> Deployment:
            async with maker() as session:
                return await _deploy_chain(session, USER_B.id, "user-b-sub/theirs")

        other = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_seed_other())

        resp = client.post(
            "/api/v1/inference/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": str(other.id), "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 404

    def test_rate_limit_exceeded_is_429(
        self, app_and_engine, client, monkeypatch, fake_redis
    ) -> None:
        _app, _engine, maker = app_and_engine
        import asyncio

        key, deployment = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            _seed_key_and_deployment(maker, rate_limit_per_min=1)
        )

        async def _fake_post_json(path, payload):
            return {
                "id": "x", "created": 0, "model": TAG_A,
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "hi"},
                     "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

        monkeypatch.setattr(inference_service, "_post_json", _fake_post_json)
        body = {"model": str(deployment.id), "messages": [{"role": "user", "content": "hi"}]}
        headers = {"Authorization": f"Bearer {key}"}

        first = client.post("/api/v1/inference/chat/completions", headers=headers, json=body)
        assert first.status_code == 200

        second = client.post("/api/v1/inference/chat/completions", headers=headers, json=body)
        assert second.status_code == 429
        assert "Retry-After" in second.headers

    def test_models_listing_via_key_never_calls_ollama(
        self, app_and_engine, client, monkeypatch, fake_redis
    ) -> None:
        _app, _engine, maker = app_and_engine
        import asyncio

        key, _deployment = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            _seed_key_and_deployment(maker)
        )

        async def _boom(path):
            raise AssertionError("must not call the Ollama daemon on the key path")

        monkeypatch.setattr(inference_service, "_get_json", _boom)

        resp = client.get(
            "/api/v1/inference/models", headers={"Authorization": f"Bearer {key}"}
        )
        assert resp.status_code == 200, resp.text
        assert [m["id"] for m in resp.json()["data"]] == [TAG_A]
