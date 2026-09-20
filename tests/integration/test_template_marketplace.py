"""Real DB/object-store acceptance, opt-in against docker/compose.template-tests.yml.

Import the two prepared templates first. Only this dedicated stack is accepted;
test-created resources remain there for inspection, never in a shared deployment.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException, Request
from minio import Minio
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration
PREPARED_ROOT = Path(__file__).resolve().parents[2] / "data/template-catalog/prepared"


@pytest.fixture
async def marketplace(monkeypatch):
    url = os.getenv("TEMPLATE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEMPLATE_TEST_DATABASE_URL and import prepared templates first")
    parsed = make_url(url)
    assert (parsed.host, parsed.port, parsed.database) == ("127.0.0.1", 15432, "template_tests"), (
        "Refuse integration mutations against any other database"
    )

    from api.core.auth import CurrentUser, require_user
    from api.core.config import Settings, get_settings

    # Do not load a developer's .env or connect to its configured services.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()

    from api.core.database import get_db
    from api.main import app
    from api.services import templates_service
    from scripts.import_template_catalog import import_catalog

    settings = Settings(
        _env_file=None,
        database_url=url,
        minio_endpoint="127.0.0.1:19000",
        minio_access_key="template_test",
        minio_secret_key="template_test_only",
        minio_datasets_bucket="template-fixtures",
        auth_required=False,
    )
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    client = Minio(
        "127.0.0.1:19000",
        access_key="template_test",
        secret_key="template_test_only",
        secure=False,
    )
    if not await asyncio.to_thread(client.bucket_exists, "template-fixtures"):
        await asyncio.to_thread(client.make_bucket, "template-fixtures")

    async def database():
        async with sessions() as db:
            yield db

    async def identity(request: Request):
        actor = request.headers.get("X-Test-User")
        return CurrentUser(id=actor, email=None) if actor else None

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[require_user] = identity
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(templates_service, "get_minio_client", lambda: client)
    monkeypatch.setattr(templates_service, "get_settings", lambda: settings)
    try:
        async with sessions() as db:
            await import_catalog(db, PREPARED_ROOT, bucket="template-fixtures", client=client)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as http:
            yield http, sessions, client, str(uuid4())
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
        await engine.dispose()


def _body(name="integration-template"):
    return {
        "name": name,
        "task_type": "classification",
        "template_id": "tpl-006",
        "template_version": "1",
        "template_overrides": {"train_sample_count": 6, "sampling_seed": 42},
    }


def _headers(actor, key=None):
    result = {"X-Test-User": actor}
    if key:
        result["Idempotency-Key"] = key
    return result


async def _create(http, actor, key=None, **changes):
    return await http.post(
        "/api/v1/projects",
        json=_body(**changes),
        headers=_headers(actor, key or str(uuid4())),
    )


async def test_catalog_auth_readiness_and_real_statistics(marketplace):
    http, _, _, actor = marketplace
    assert (await http.get("/api/v1/templates")).status_code == 401
    result = await http.get("/api/v1/templates", headers=_headers(actor))
    assert result.status_code == 200, result.text
    page = result.json()
    assert {item["id"] for item in page["items"]} == {"tpl-006"}
    assert all(item["author"] == "SLM Studio Team" for item in page["items"])
    assert all(item["my_rating"] is None for item in page["items"])
    all_items = (
        await http.get("/api/v1/templates?include_unavailable=true", headers=_headers(actor))
    ).json()
    assert all_items["total"] == 7
    assert len(all_items["items"]) == 7


async def test_concurrent_replay_copies_exactly_once_and_conflicts(marketplace):
    from api.models import Dataset, Project
    from api.models.template import TemplateUse

    http, sessions, minio, actor = marketplace
    key = str(uuid4())
    responses = await asyncio.gather(*[_create(http, actor, key) for _ in range(3)])
    assert all(response.status_code == 201 for response in responses), [r.text for r in responses]
    assert responses[0].json() == responses[1].json() == responses[2].json()
    project = responses[0].json()
    async with sessions() as db:
        assert (
            await db.scalar(
                select(func.count()).select_from(Project).where(Project.owner_id == actor)
            )
            == 1
        )
        assert (
            await db.scalar(
                select(func.count()).select_from(TemplateUse).where(TemplateUse.user_id == actor)
            )
            == 1
        )
        datasets = (await db.scalars(select(Dataset).where(Dataset.owner_id == actor))).all()
        assert len(datasets) == 3
        assert {d.generation_metadata["role"]: d.num_samples for d in datasets} == {
            "train": 6,
            "validation": 600,
            "test": 900,
        }
        from workers.storage import parse_s3_uri

        for dataset in datasets:
            bucket, path = parse_s3_uri(dataset.storage_uri)
            response = minio.get_object(bucket, path)
            try:
                blob = response.read()
            finally:
                response.close()
                response.release_conn()
            assert hashlib.sha256(blob).hexdigest() == dataset.generation_metadata["sha256"]
            assert dataset.project_id == UUID(project["id"])
            assert dataset.parent_dataset_id is None
    conflict = await _create(http, actor, key, name="different-payload")
    assert conflict.status_code == 409, conflict.text


async def test_rating_eligibility_upsert_and_replay_after_project_delete(marketplace):
    from api.models import Dataset
    from api.models.template import TemplateUse

    http, sessions, _, actor = marketplace
    rating_url = "/api/v1/templates/tpl-006/rating"
    assert (
        await http.put(rating_url, json={"rating": 5}, headers=_headers(actor))
    ).status_code == 403
    key = str(uuid4())
    created = await _create(http, actor, key)
    assert created.status_code == 201, created.text
    for rating in [5, 3]:
        rated = await http.put(rating_url, json={"rating": rating}, headers=_headers(actor))
        assert rated.status_code == 200, rated.text
        assert rated.json()["my_rating"] == rating
    removed = await http.delete("/api/v1/projects/" + created.json()["id"], headers=_headers(actor))
    assert removed.status_code == 204, removed.text
    replay = await _create(http, actor, key)
    assert replay.status_code == 201 and replay.json() == created.json()
    assert (
        await http.put(rating_url, json={"rating": 4}, headers=_headers(actor))
    ).status_code == 200
    async with sessions() as db:
        assert (
            await db.scalar(
                select(func.count()).select_from(TemplateUse).where(TemplateUse.user_id == actor)
            )
            == 1
        )
        retained = (await db.scalars(select(Dataset).where(Dataset.owner_id == actor))).all()
        assert len(retained) == 3 and all(d.project_id is None for d in retained)


async def test_independent_copies_reproducible_balanced_sampling_and_frozen_defaults(marketplace):
    from api.models import Dataset, Project
    from api.models.template import TemplateDatasetVersion
    from workers.storage import parse_s3_uri

    http, sessions, client, actor = marketplace
    first = await _create(http, actor, name="first-copy")
    second = await _create(http, actor, name="second-copy")
    assert first.status_code == second.status_code == 201
    left, right = first.json()["template_snapshot"], second.json()["template_snapshot"]
    async with sessions() as db:
        original = await db.get(TemplateDatasetVersion, ("tpl-006", "1"))
        assert original is not None
        originals = {entry["storage_uri"] for entry in original.splits_json.values()}
        copies = (await db.scalars(select(Dataset).where(Dataset.owner_id == actor))).all()
        assert len({d.storage_uri for d in copies}) == 6
        assert not originals.intersection(d.storage_uri for d in copies)
        for role in ["train", "validation", "test"]:
            assert left[f"{role}_dataset_id"] != right[f"{role}_dataset_id"]
            assert left[f"{role}_sha256"] == right[f"{role}_sha256"]
        for role in ["validation", "test"]:
            assert left[f"{role}_sha256"] == original.splits_json[role]["sha256"]
        train = next(d for d in copies if str(d.id) == left["train_dataset_id"])
        response = client.get_object(*parse_s3_uri(train.storage_uri))
        try:
            rows = [json.loads(line) for line in response.read().splitlines() if line]
        finally:
            response.close()
            response.release_conn()
        assert Counter(row["label"] for row in rows) == {"positive": 2, "neutral": 2, "negative": 2}
        # Version/config data is persisted, not computed from a later catalog read.
        saved = await db.get(Project, UUID(first.json()["id"]))
        assert saved.template_snapshot == left


async def test_strict_rating_and_template_creation_guards(marketplace):
    http, _, _, actor = marketplace
    assert (
        await http.post("/api/v1/projects", json=_body(), headers=_headers(actor))
    ).status_code in {400, 422}
    assert (
        await http.post("/api/v1/projects", json=_body(), headers={"Idempotency-Key": str(uuid4())})
    ).status_code == 401
    for value in [True, 1.5, "5", 0, 6]:
        response = await http.put(
            "/api/v1/templates/tpl-006/rating", json={"rating": value}, headers=_headers(actor)
        )
        assert response.status_code == 422, response.text


async def test_retained_training_and_model_remain_private_and_accessible(marketplace):
    from api.models import ModelArtifact, TrainingJob
    from api.schemas.enums import JobStatus, TrainingMode

    http, sessions, _, actor = marketplace
    created = await _create(http, actor)
    assert created.status_code == 201, created.text
    project = created.json()
    snapshot = project["template_snapshot"]
    async with sessions() as db:
        training = TrainingJob(
            project_id=UUID(project["id"]),
            dataset_id=UUID(snapshot["train_dataset_id"]),
            owner_id=actor,
            mode=TrainingMode.MANUAL,
            status=JobStatus.COMPLETED,
            base_model=snapshot["base_model"],
            config_json={},
            context_snapshot=snapshot,
        )
        db.add(training)
        await db.flush()
        artifact = ModelArtifact(
            training_job_id=training.id,
            name="retained-fixture",
            base_model=training.base_model,
        )
        db.add(artifact)
        await db.commit()
        training_id, model_id = str(training.id), str(artifact.id)
    deleted = await http.delete("/api/v1/projects/" + project["id"], headers=_headers(actor))
    assert deleted.status_code == 204, deleted.text
    stranger = str(uuid4())
    for path in [
        "/api/v1/trainings/" + training_id,
        "/api/v1/models/" + model_id,
        "/api/v1/datasets/" + snapshot["train_dataset_id"],
    ]:
        owned = await http.get(path, headers=_headers(actor))
        assert owned.status_code == 200, owned.text
        if path.startswith("/api/v1/trainings/"):
            assert owned.json()["context_snapshot"] == snapshot
        assert (await http.get(path, headers=_headers(stranger))).status_code == 403


async def test_real_training_snapshots_and_heldout_guards_without_gpu(marketplace, monkeypatch):
    from api.core.auth import CurrentUser
    from api.models import TrainingJob
    from api.schemas.training import HPOTrainingRequest, ManualTrainingRequest
    from api.services import training_service
    from workers.tasks.hpo_training import train_hpo
    from workers.tasks.training import train_manual

    http, sessions, _, actor = marketplace
    created = await _create(http, actor)
    assert created.status_code == 201, created.text
    project = created.json()
    snapshot = project["template_snapshot"]
    no_quota = AsyncMock()
    monkeypatch.setattr(training_service.quota, "assert_can_submit", no_quota)
    manual_queue = Mock(return_value=SimpleNamespace(id=str(uuid4())))
    hpo_queue = Mock(return_value=SimpleNamespace(id=str(uuid4())))
    monkeypatch.setattr(train_manual, "apply_async", manual_queue)
    monkeypatch.setattr(train_hpo, "apply_async", hpo_queue)
    user = CurrentUser(id=actor, email=None)
    for request_class, submit, mode, extra in [
        (ManualTrainingRequest, training_service.submit_manual_training_job, "manual", {}),
        (
            HPOTrainingRequest,
            training_service.submit_hpo_training_job,
            "hpo",
            {
                "hpo_config": {
                    "n_trials": 2,
                    "search_space": {"learning_rate": {"low": 0.0001, "high": 0.0003}},
                }
            },
        ),
    ]:
        async with sessions() as db:
            for role in ["validation", "test"]:
                request = request_class(
                    mode=mode,
                    project_id=project["id"],
                    dataset_id=snapshot[f"{role}_dataset_id"],
                    **extra,
                )
                with pytest.raises(HTTPException) as rejected:
                    await submit(db, request, user=user)
                assert rejected.value.status_code == 422
            request = request_class(
                mode=mode,
                project_id=project["id"],
                dataset_id=snapshot["train_dataset_id"],
                system_prompt="Fixture prompt override",
                train_sample_count=3,
                **extra,
            )
            accepted = await submit(db, request, user=user)
            job = await db.get(TrainingJob, accepted.training_id)
            assert job.owner_id == actor
            assert job.context_snapshot["system_prompt"] == "Fixture prompt override"
            assert job.context_snapshot["manual_config"]["num_train_epochs"] == 2
            assert job.context_snapshot["train_sample_count"] == 3
            assert (
                job.context_snapshot["validation_dataset_id"] == snapshot["validation_dataset_id"]
            )
            assert "system_prompt" not in job.config_json
    assert manual_queue.call_count == hpo_queue.call_count == 1
    assert no_quota.await_count == 2


@pytest.mark.parametrize("fail_at", [2, 3])
async def test_failed_copy_rolls_back_usage_and_cleans_only_new_objects(
    marketplace, monkeypatch, fail_at
):
    from api.models import Dataset, Project
    from api.models.template import TemplateUse
    from api.services import templates_service

    http, sessions, client, actor = marketplace
    bucket = "template-fixtures"
    before = {obj.object_name: obj.etag for obj in client.list_objects(bucket, recursive=True)}

    class FailCopy:
        writes = 0

        def __getattr__(self, name):
            return getattr(client, name)

        def _write(self, name, *args, **kwargs):
            self.writes += 1
            if self.writes == fail_at:
                raise RuntimeError("injected isolated MinIO write failure")
            return getattr(client, name)(*args, **kwargs)

        def put_object(self, *args, **kwargs):
            return self._write("put_object", *args, **kwargs)

        def copy_object(self, *args, **kwargs):
            return self._write("copy_object", *args, **kwargs)

    faulty = FailCopy()
    monkeypatch.setattr(templates_service, "get_minio_client", lambda: faulty)
    response = await _create(http, actor)
    assert response.status_code >= 500, response.text
    assert faulty.writes == fail_at
    async with sessions() as db:
        for model, owner in [
            (Project, Project.owner_id),
            (Dataset, Dataset.owner_id),
            (TemplateUse, TemplateUse.user_id),
        ]:
            assert (
                await db.scalar(select(func.count()).select_from(model).where(owner == actor)) == 0
            )
    after = {obj.object_name: obj.etag for obj in client.list_objects(bucket, recursive=True)}
    assert after == before
