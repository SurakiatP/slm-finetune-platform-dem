"""Concurrent deployment creates can't overshoot the caps (real PostgreSQL).

sqlite has no advisory locks and serializes writers anyway, so the race only
exists — and the fix can only be proven — against Postgres. Opt in with
`DEPLOYMENT_TEST_DATABASE_URL` pointing at a throwaway database; the schema
is built with `create_all`, so never point it at a real one.
"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.core.auth import CurrentUser
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.deployment import Deployment
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.deployments import DeploymentCreate
from api.schemas.enums import DatasetSource, JobStatus, TaskType, TrainingMode
from api.services import deployments_service

pytestmark = pytest.mark.integration

_ATTEMPTS = 8


@pytest.fixture
async def maker(monkeypatch):
    url = os.getenv("DEPLOYMENT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set DEPLOYMENT_TEST_DATABASE_URL to a throwaway Postgres database")
    engine = create_async_engine(url, pool_size=_ATTEMPTS + 2)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    import workers.tasks.deployment as deployment_task

    monkeypatch.setattr(deployment_task.preload_deployment, "apply_async", lambda **kw: None)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _artifacts(maker, owner_id: str, n: int) -> list:
    """`n` exported artifacts for `owner_id` (one training each — an artifact
    is unique per training job)."""
    ids = []
    async with maker() as session:
        project = Project(id=uuid4(), name="p", task_type=TaskType.QA, owner_id=owner_id)
        dataset = Dataset(
            id=uuid4(), project_id=project.id, name="ds", task_type=TaskType.QA,
            source=DatasetSource.SEED, status=JobStatus.COMPLETED, num_samples=1,
        )
        session.add(project)
        await session.flush()
        session.add(dataset)
        await session.flush()
        for i in range(n):
            training = TrainingJob(
                id=uuid4(), project_id=project.id, owner_id=owner_id, dataset_id=dataset.id,
                mode=TrainingMode.MANUAL, status=JobStatus.COMPLETED,
                base_model="unsloth/Llama-3.2-1B-Instruct-bnb-4bit", config_json={},
            )
            session.add(training)
            await session.flush()
            artifact = ModelArtifact(
                id=uuid4(), training_job_id=training.id, name=f"m{i}",
                base_model=training.base_model, lora_adapter_uri="s3://models/x",
                ollama_model_tag=f"tag/{owner_id}/{i}",
            )
            session.add(artifact)
            ids.append(artifact.id)
        await session.commit()
    return ids


async def _create(maker, artifact_id, user: CurrentUser) -> int:
    async with maker() as session:
        try:
            await deployments_service.create_deployment(
                session, DeploymentCreate(model_artifact_id=artifact_id), user
            )
            return 201
        except HTTPException as exc:
            return exc.status_code


async def _active(maker, **where) -> int:
    async with maker() as session:
        stmt = select(func.count()).select_from(Deployment).where(
            Deployment.status.in_(deployments_service.ACTIVE)
        )
        for column, value in where.items():
            stmt = stmt.where(getattr(Deployment, column) == value)
        return int((await session.execute(stmt)).scalar_one())


async def test_concurrent_creates_respect_per_owner_cap(maker):
    """Default cap is 1/user: of 8 simultaneous creates on 8 artifacts, one wins."""
    user = CurrentUser(id="racer", email=None)
    ids = await _artifacts(maker, user.id, _ATTEMPTS)
    codes = await asyncio.gather(*(_create(maker, i, user) for i in ids))
    assert sorted(codes) == [201] + [429] * (_ATTEMPTS - 1)
    assert await _active(maker, owner_id=user.id) == 1


async def test_concurrent_creates_on_one_artifact_allow_one(maker):
    """One active deployment per artifact, even when requests collide."""
    users = [CurrentUser(id=f"same-{i}", email=None) for i in range(_ATTEMPTS)]
    (artifact_id,) = await _artifacts(maker, users[0].id, 1)
    codes = await asyncio.gather(*(_create(maker, artifact_id, users[0]) for _ in users))
    assert codes.count(201) == 1
    assert await _active(maker, model_artifact_id=artifact_id) == 1


async def test_concurrent_creates_respect_global_cap(maker):
    """Default global cap is 3: 8 different owners at once → exactly 3 win."""
    users = [CurrentUser(id=f"owner-{i}", email=None) for i in range(_ATTEMPTS)]
    ids = [(await _artifacts(maker, u.id, 1))[0] for u in users]
    codes = await asyncio.gather(*(_create(maker, a, u) for a, u in zip(ids, users, strict=True)))
    assert codes.count(201) == 3
    assert await _active(maker) == 3
