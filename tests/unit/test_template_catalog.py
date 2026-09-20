"""Marketplace validation and byte-level source/copy invariants."""

import hashlib
import json
from collections import Counter
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from api.schemas.enums import TaskType
from api.schemas.projects import ProjectCreate
from api.schemas.templates import TemplateOverrides, TemplateRatingRequest
from api.services import templates_service as service
from scripts.import_template_catalog import verify_prepared


def test_curated_catalog_excludes_unsupported_ner_template():
    definitions = service.load_catalog()
    assert len(definitions) == 7
    assert {d["id"] for d in definitions if d["data_ready"]} == {"tpl-006"}
    assert {d["id"] for d in definitions} == {
        "tpl-001",
        "tpl-002",
        "tpl-003",
        "tpl-005",
        "tpl-006",
        "tpl-007",
        "tpl-008",
    }
    assert all(d["author"] == "SLM Studio Team" for d in definitions)
    assert all("rating" not in d and "forks" not in d for d in definitions)
    with pytest.raises(HTTPException) as exc:
        service.get_definition("tpl-004")
    assert exc.value.status_code == 404


@pytest.mark.parametrize("rating", [True, 1.0, "5", 0, 6])
def test_rating_is_strict_integer(rating):
    with pytest.raises(ValidationError):
        TemplateRatingRequest(rating=rating)


@pytest.mark.parametrize(
    "override",
    [
        {"epochs": 0},
        {"epochs": 21},
        {"learning_rate": 0},
        {"learning_rate": float("nan")},
        {"train_sample_count": True},
        {"sampling_seed": -1},
        {"base_model": "unsupported"},
        {"system_prompt": ""},
    ],
)
def test_overrides_reject_invalid_values(override):
    with pytest.raises(ValidationError):
        TemplateOverrides(**override)


def test_template_fields_cannot_silently_apply_to_ordinary_project():
    with pytest.raises(ValidationError):
        ProjectCreate(name="test", task_type="qa", template_version="1")


def test_train_sampling_is_exact_reproducible_and_stratified():
    rows = [{"text": str(i), "label": str(i % 3)} for i in range(90)]
    sampled = service.sample_train(rows, TaskType.CLASSIFICATION, 9, 123)
    assert sampled == service.sample_train(rows, TaskType.CLASSIFICATION, 9, 123)
    assert Counter(r["label"] for r in sampled) == {"0": 3, "1": 3, "2": 3}
    assert service.sample_train(rows, TaskType.CLASSIFICATION, 90, 123) == rows
    with pytest.raises(HTTPException):
        service.sample_train(rows, TaskType.CLASSIFICATION, 91, 123)


def test_real_prepared_manifests_verify_without_network():
    root = Path(__file__).parents[2] / "data/template-catalog/prepared"
    if not (root / "tpl-006/manifest.json").exists():
        pytest.skip("prepared source data is intentionally not distributed in git")
    manifest, payloads = verify_prepared(root / "tpl-006", service.get_definition("tpl-006"))
    assert [len(payloads[r].splitlines()) for r in service.ROLES] == [6000, 600, 900]
    assert manifest["template_id"] == "tpl-006"


def test_catalog_definition_hash_is_order_independent():
    assert service.definition_hash({"a": 1, "b": 2}) == service.definition_hash({"b": 2, "a": 1})


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    definition = service.get_definition("tpl-006")
    directory = tmp_path / "tpl-006"
    directory.mkdir()
    payload = b'{"text":"one","label":"positive"}\n{"text":"two","label":"negative"}\n'
    digest = hashlib.sha256(payload).hexdigest()
    provenance = b'{"id":1}\n{"id":2}\n'
    manifest = {
        "status": "prepared",
        "template_id": "tpl-006",
        "task_type": "classification",
        "source": {
            "attribution": "Synthetic unit fixture",
            "license": "CC0",
            "repo": "fixture",
            "revision": "1",
        },
        "license_file": {"path": "LICENSE", "sha256": hashlib.sha256(b"CC0").hexdigest()},
        "tokenizer": {"max_tokens": 2048},
        "splits": {},
    }
    (directory / "LICENSE").write_bytes(b"CC0")
    for role in service.ROLES:
        (directory / f"{role}.jsonl").write_bytes(payload)
        (directory / f"{role}.provenance.jsonl").write_bytes(provenance)
        manifest["splits"][role] = dict(
            path=f"{role}.jsonl",
            rows=2,
            sha256=digest,
            shortfall=0,
            max_tokens=12,
            provenance_path=f"{role}.provenance.jsonl",
            provenance_sha256=hashlib.sha256(provenance).hexdigest(),
        )
    definition["split_counts"] = dict.fromkeys(service.ROLES, 2)
    definition["source_sha256"] = dict.fromkeys(service.ROLES, digest)
    (directory / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(service, "load_catalog", lambda: [definition])
    return directory, definition, manifest


@pytest.mark.parametrize(
    "alteration", ["bytes", "manifest_and_bytes", "count", "schema", "path", "license"]
)
def test_registration_rejects_modified_prepared_data(prepared, alteration):
    directory, definition, manifest = prepared
    if alteration in {"bytes", "manifest_and_bytes", "schema"}:
        payload = b'{"text":"tampered"}\n'
        (directory / "train.jsonl").write_bytes(payload)
        if alteration != "bytes":
            manifest["splits"]["train"]["sha256"] = hashlib.sha256(payload).hexdigest()
        if alteration == "schema":
            # Even an operator-approved hash cannot bypass canonical schema validation.
            definition["source_sha256"]["train"] = manifest["splits"]["train"]["sha256"]
            definition["split_counts"]["train"] = manifest["splits"]["train"]["rows"] = 1
    elif alteration == "count":
        manifest["splits"]["train"]["rows"] = 1
    elif alteration == "path":
        manifest["splits"]["train"]["path"] = "../train.jsonl"
    else:
        (directory / "LICENSE").write_text("changed")
    (directory / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        verify_prepared(directory, definition)


@pytest.fixture
async def catalog_db():
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.ext.compiler import compiles

    from api.models.base import Base

    @compiles(JSONB, "sqlite")
    def jsonb_as_json(*_args, **_kwargs):
        return "JSON"

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        yield db
    await engine.dispose()


async def test_import_repeat_never_overwrites_and_changed_definition_rejected(
    catalog_db, prepared, fake_minio
):
    from scripts.import_template_catalog import import_catalog

    directory, definition, _ = prepared
    kwargs = dict(template_ids=["tpl-006"], bucket="fixtures", client=fake_minio)
    rows = await import_catalog(catalog_db, directory.parent, **kwargs)
    before = dict(fake_minio._store)
    again = await import_catalog(catalog_db, directory.parent, **kwargs)
    assert rows[0].splits_json == again[0].splits_json
    assert fake_minio._store == before
    definition["prompt"] = "changed without new version"
    with pytest.raises(ValueError, match="Immutable registration"):
        await import_catalog(catalog_db, directory.parent, **kwargs)
    assert fake_minio._store == before


async def test_materialization_failure_cleans_copies_and_rolls_back(
    catalog_db, prepared, fake_minio, monkeypatch
):
    from types import SimpleNamespace

    from sqlalchemy import func, select

    from api.core.auth import CurrentUser
    from api.models import Dataset, Project
    from api.models.template import TemplateUse
    from scripts.import_template_catalog import import_catalog

    directory, _, _ = prepared
    await import_catalog(
        catalog_db, directory.parent, template_ids=["tpl-006"], bucket="fixtures", client=fake_minio
    )
    sources = dict(fake_minio._store)
    monkeypatch.setattr(service, "get_minio_client", lambda: fake_minio)
    monkeypatch.setattr(
        service, "get_settings", lambda: SimpleNamespace(minio_datasets_bucket="copies")
    )
    put = fake_minio.put_object

    def failing_put(bucket, key, *args, **kwargs):
        put(bucket, key, *args, **kwargs)
        if "/validation/" in key:
            raise RuntimeError("simulated write failure after storing bytes")

    monkeypatch.setattr(fake_minio, "put_object", failing_put)
    body = ProjectCreate(name="failure", task_type="classification", template_id="tpl-006")
    with pytest.raises(HTTPException) as caught:
        await service.create_template_project(
            catalog_db, body, CurrentUser(str(uuid4()), None), "failure-key"
        )
    assert caught.value.status_code == 503
    assert fake_minio._store == sources
    for model in (Project, Dataset, TemplateUse):
        assert await catalog_db.scalar(select(func.count()).select_from(model)) == 0


async def test_materialization_replay_survives_delete_and_validates_source(
    catalog_db, prepared, fake_minio, monkeypatch
):
    from types import SimpleNamespace

    from sqlalchemy import delete, func, select

    from api.core.auth import CurrentUser
    from api.models import Project
    from api.models.template import TemplateUse
    from scripts.import_template_catalog import import_catalog

    directory, _, _ = prepared
    await import_catalog(
        catalog_db, directory.parent, template_ids=["tpl-006"], bucket="fixtures", client=fake_minio
    )
    monkeypatch.setattr(service, "get_minio_client", lambda: fake_minio)
    monkeypatch.setattr(
        service, "get_settings", lambda: SimpleNamespace(minio_datasets_bucket="copies")
    )
    user = CurrentUser(str(uuid4()), None)
    body = ProjectCreate(
        name="copy",
        task_type="classification",
        template_id="tpl-006",
        template_overrides={"train_sample_count": 1},
    )
    first = await service.create_template_project(catalog_db, body, user, "key")
    assert first.template_snapshot["train_sample_count"] == 1
    await catalog_db.execute(delete(Project).where(Project.id == first.id))
    await catalog_db.commit()
    before = dict(fake_minio._store)
    assert await service.create_template_project(catalog_db, body, user, "key") == first
    assert fake_minio._store == before
    with pytest.raises(HTTPException) as conflict:
        await service.create_template_project(
            catalog_db, body.model_copy(update={"name": "different"}), user, "key"
        )
    assert conflict.value.status_code == 409
    source_key = next(key for key in fake_minio._store if key[0] == "fixtures")
    fake_minio._store[source_key] = b"changed"
    with pytest.raises(HTTPException) as corrupt:
        await service.create_template_project(catalog_db, body, user, "new-key")
    assert corrupt.value.status_code == 503
    assert await catalog_db.scalar(select(func.count()).select_from(TemplateUse)) == 1
