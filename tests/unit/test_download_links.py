"""Layer 1 — presigned-download-URL endpoint behaviour, against `_FakeMinio`.

Covers `api/services/download_links.py`'s policy layer end to end: ownership
(404 for a non-owner), the audit row written at mint time, the PDF fallback
for seed-PDF datasets, multi-file listing for `lora`/`safetensors` model
exports, the listing cap, and the 503 when `MINIO_PUBLIC_URL` is unset.

This layer deliberately CANNOT catch host/region/path-style bugs — `_FakeMinio.
presigned_get_object` never touches SigV4 at all. That's what
`tests/unit/test_presign_url_shape.py` (Layer 2, a real `Minio` client, no
network) exists for; see its module docstring.

**Import-by-name trap**: `api/services/download_links.py` does
`from workers.storage import get_presign_client, get_minio_client` at module
scope, binding its own local aliases. Patching `workers.storage.
get_presign_client` after that import does NOT reach `download_links`'s copy
— every monkeypatch below targets `download_links.get_presign_client` /
`download_links.get_minio_client` directly (and `model_service.
get_minio_client`, for the gguf path which reuses `_first_gguf_object`).
Same footgun `tests/unit/test_dataset_status.py:191,277` documents.

In-memory aiosqlite; no Postgres, no network. Same `@compiles(JSONB,
"sqlite")` shim `test_dataset_status.py` / `test_ownership.py` use.
"""

from __future__ import annotations

from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from api.core.auth import CurrentUser
from api.core.config import get_settings
from api.models.audit_event import AuditEvent
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.enums import ArtifactFormat, DatasetSource, JobStatus, TaskType, TrainingMode
from api.services import download_links
from api.services import model_service
from workers.storage import s3_uri


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


ALICE = CurrentUser(id="alice-sub", email="alice@example.com")
BOB = CurrentUser(id="bob-sub", email="bob@example.com")

_BUCKET_DATASETS = "datasets"
_BUCKET_MODELS = "models"


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def presigned_url_configured(monkeypatch: pytest.MonkeyPatch):
    """`MINIO_PUBLIC_URL` set + settings cache cleared, for the duration of
    a test — the config-guard tests are the ones that want it *unset*, so
    this is opt-in rather than autouse."""
    monkeypatch.setenv("MINIO_PUBLIC_URL", "https://storage.example.com")
    monkeypatch.setenv("PRESIGNED_URL_TTL_SECONDS", "300")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def patch_presign(monkeypatch: pytest.MonkeyPatch, fake_minio, presigned_url_configured):
    """Route `download_links`'s (and, for the gguf path, `model_service`'s)
    minio factories to the same in-memory fake, per the import-by-name trap
    documented at the top of this file."""
    monkeypatch.setattr(download_links, "get_presign_client", lambda: fake_minio)
    monkeypatch.setattr(download_links, "get_minio_client", lambda: fake_minio)
    monkeypatch.setattr(model_service, "get_minio_client", lambda: fake_minio)
    return fake_minio


async def _project_and_dataset(
    db: AsyncSession,
    *,
    owner: str | None,
    storage_uri: str | None,
    generation_metadata: dict | None = None,
) -> tuple[Project, Dataset]:
    project = Project(id=uuid4(), name="p", task_type=TaskType.QA, owner_id=owner)
    dataset = Dataset(
        id=uuid4(),
        project_id=project.id,
        name="seed-ds",
        task_type=TaskType.QA,
        source=DatasetSource.SEED,
        status=JobStatus.COMPLETED,
        num_samples=1,
        storage_uri=storage_uri,
        generation_metadata=generation_metadata,
    )
    db.add_all([project, dataset])
    await db.commit()
    return project, dataset


async def _project_training_and_artifact(
    db: AsyncSession,
    *,
    owner: str | None,
    gguf_uri: str | None = None,
    safetensors_uri: str | None = None,
    lora_adapter_uri: str | None = None,
) -> tuple[Project, ModelArtifact]:
    project = Project(id=uuid4(), name="p", task_type=TaskType.QA, owner_id=owner)
    dataset = Dataset(
        id=uuid4(), project_id=project.id, name="d", task_type=TaskType.QA,
        source=DatasetSource.SDG, status=JobStatus.COMPLETED,
    )
    training = TrainingJob(
        id=uuid4(), project_id=project.id, dataset_id=dataset.id, mode=TrainingMode.MANUAL,
        status=JobStatus.COMPLETED, base_model="unsloth/x", config_json={},
    )
    artifact = ModelArtifact(
        id=uuid4(), training_job_id=training.id, name="m", base_model="unsloth/x",
        gguf_uri=gguf_uri, safetensors_uri=safetensors_uri, lora_adapter_uri=lora_adapter_uri,
    )
    db.add_all([project, dataset, training, artifact])
    await db.commit()
    return project, artifact


async def _audit_rows(db: AsyncSession, action: str) -> list[AuditEvent]:
    """Rows that survived a rollback — i.e. rows that were really COMMITTED.

    Querying the same session that wrote them proves nothing: SQLAlchemy
    autoflushes pending objects before a SELECT, so an uncommitted row is
    returned exactly like a committed one. Deleting BOTH `await db.commit()`
    calls from `api/services/download_links.py` left this file fully green
    until this rollback was added.

    That matters because `api/core/database.py`'s `get_db` yields and closes
    without committing — so a missing commit here silently drops every
    `dataset.download_url` / `model.download_url` audit row, on a code path
    whose entire purpose is to leave a trail of who was handed a capability
    URL. The service commits deliberately and before returning the URL, for
    the same reason `datasets_service.py` does on the streaming download:
    the record must exist before the credential escapes.
    """
    await db.rollback()
    rows = (await db.execute(select(AuditEvent).where(AuditEvent.action == action))).scalars().all()
    return list(rows)


# =============================================================================
# 1. 503 when MINIO_PUBLIC_URL is unset — checked before ownership/DB
# =============================================================================


class TestPresignNotConfigured:
    async def test_dataset_mint_503s_when_public_url_unset(
        self, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MINIO_PUBLIC_URL", "")
        get_settings.cache_clear()
        try:
            with pytest.raises(HTTPException) as exc:
                await download_links.mint_dataset_download_url(db, uuid4(), ALICE)
            assert exc.value.status_code == 503
        finally:
            get_settings.cache_clear()

    async def test_model_mint_503s_when_public_url_unset(
        self, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MINIO_PUBLIC_URL", "")
        get_settings.cache_clear()
        try:
            with pytest.raises(HTTPException) as exc:
                await download_links.mint_model_download_url(
                    db, uuid4(), ArtifactFormat.GGUF, ALICE
                )
            assert exc.value.status_code == 503
        finally:
            get_settings.cache_clear()


# =============================================================================
# 2. Dataset download-url
# =============================================================================


class TestDatasetDownloadUrl:
    async def test_owner_gets_a_url_and_an_audit_row(
        self, db: AsyncSession, patch_presign
    ) -> None:
        uri = s3_uri(_BUCKET_DATASETS, "seeds/d1.jsonl")
        _project, dataset = await _project_and_dataset(db, owner=ALICE.id, storage_uri=uri)

        resp = await download_links.mint_dataset_download_url(db, dataset.id, ALICE)

        assert resp.filename == "seed-ds.jsonl"
        assert resp.content_type == "application/x-ndjson"
        assert resp.expires_in == 300
        assert "fake-presigned.example.com" in resp.url
        assert _BUCKET_DATASETS in resp.url

        rows = await _audit_rows(db, "dataset.download_url")
        assert len(rows) == 1
        assert rows[0].resource_id == str(dataset.id)
        assert rows[0].event_metadata["pdf_fallback"] is False

    async def test_non_owner_gets_404_not_403(self, db: AsyncSession, patch_presign) -> None:
        uri = s3_uri(_BUCKET_DATASETS, "seeds/d1.jsonl")
        _project, dataset = await _project_and_dataset(db, owner=ALICE.id, storage_uri=uri)

        with pytest.raises(HTTPException) as exc:
            await download_links.mint_dataset_download_url(db, dataset.id, BOB)
        assert exc.value.status_code == 404

    async def test_pdf_fallback_when_storage_uri_is_null(
        self, db: AsyncSession, patch_presign
    ) -> None:
        """The gap this endpoint closes for free: PDF-seeded datasets have
        `storage_uri = None`; `datasets_service._persist_pdf_dataset` puts the
        object at `generation_metadata['pdf_uri']` instead."""
        pdf_uri = s3_uri(_BUCKET_DATASETS, "seed-pdfs/d1.pdf")
        _project, dataset = await _project_and_dataset(
            db, owner=ALICE.id, storage_uri=None, generation_metadata={"pdf_uri": pdf_uri}
        )

        resp = await download_links.mint_dataset_download_url(db, dataset.id, ALICE)

        assert resp.filename == "seed-ds.pdf"
        assert resp.content_type == "application/pdf"

        rows = await _audit_rows(db, "dataset.download_url")
        assert rows[0].event_metadata["pdf_fallback"] is True

    async def test_409_when_nothing_to_download(self, db: AsyncSession, patch_presign) -> None:
        _project, dataset = await _project_and_dataset(db, owner=ALICE.id, storage_uri=None)

        with pytest.raises(HTTPException) as exc:
            await download_links.mint_dataset_download_url(db, dataset.id, ALICE)
        assert exc.value.status_code == 409


# =============================================================================
# 3. Model download-url
# =============================================================================


class TestModelDownloadUrl:
    async def test_non_owner_gets_404(self, db: AsyncSession, patch_presign) -> None:
        _project, artifact = await _project_training_and_artifact(
            db, owner=ALICE.id, gguf_uri=s3_uri(_BUCKET_MODELS, "exports/m1")
        )
        with pytest.raises(HTTPException) as exc:
            await download_links.mint_model_download_url(
                db, artifact.id, ArtifactFormat.GGUF, BOB
            )
        assert exc.value.status_code == 404

    async def test_gguf_returns_one_file(self, db: AsyncSession, patch_presign) -> None:
        prefix = "exports/m1"
        patch_presign.put_object(
            _BUCKET_MODELS, f"{prefix}/model.Q4_K_M.gguf", BytesIO(b"gguf-bytes"), length=10
        )
        _project, artifact = await _project_training_and_artifact(
            db, owner=ALICE.id, gguf_uri=s3_uri(_BUCKET_MODELS, prefix)
        )

        resp = await download_links.mint_model_download_url(
            db, artifact.id, ArtifactFormat.GGUF, ALICE
        )

        assert resp.format is ArtifactFormat.GGUF
        assert len(resp.files) == 1
        assert resp.files[0].key.endswith(".gguf")
        assert resp.truncated is False

        rows = await _audit_rows(db, "model.download_url")
        assert len(rows) == 1
        assert rows[0].event_metadata == {"format": "gguf", "file_count": 1}

    async def test_lora_lists_multiple_files(self, db: AsyncSession, patch_presign) -> None:
        prefix = "lora/m1"
        for name in ("adapter_config.json", "adapter_model.safetensors", "README.md"):
            patch_presign.put_object(
                _BUCKET_MODELS, f"{prefix}/{name}", BytesIO(b"x"), length=1
            )
        _project, artifact = await _project_training_and_artifact(
            db, owner=ALICE.id, lora_adapter_uri=s3_uri(_BUCKET_MODELS, prefix)
        )

        resp = await download_links.mint_model_download_url(
            db, artifact.id, ArtifactFormat.LORA, ALICE
        )

        assert resp.format is ArtifactFormat.LORA
        assert {f.name for f in resp.files} == {
            "adapter_config.json",
            "adapter_model.safetensors",
            "README.md",
        }
        assert resp.truncated is False

        rows = await _audit_rows(db, "model.download_url")
        assert rows[0].event_metadata == {"format": "lora", "file_count": 3}

    async def test_safetensors_400_leak_is_fixed_on_the_streaming_endpoint(
        self, db: AsyncSession, patch_presign
    ) -> None:
        """Task 4: `download_artifact` (the streaming endpoint) must no
        longer echo the raw `s3://` URI in its 400 detail."""
        prefix = "safetensors/m1"
        uri = s3_uri(_BUCKET_MODELS, prefix)
        _project, artifact = await _project_training_and_artifact(
            db, owner=ALICE.id, safetensors_uri=uri
        )

        with pytest.raises(HTTPException) as exc:
            await model_service.download_artifact(
                db, model_id=artifact.id, fmt="safetensors", user=ALICE
            )
        assert exc.value.status_code == 400
        assert uri not in str(exc.value.detail)
        assert "s3://" not in str(exc.value.detail)
        assert "download-url" in str(exc.value.detail)

    async def test_listing_is_capped(self, db: AsyncSession, patch_presign) -> None:
        prefix = "lora/big"
        for i in range(download_links.MAX_LISTING_OBJECTS + 5):
            patch_presign.put_object(
                _BUCKET_MODELS, f"{prefix}/file-{i:04d}.bin", BytesIO(b"x"), length=1
            )
        _project, artifact = await _project_training_and_artifact(
            db, owner=ALICE.id, lora_adapter_uri=s3_uri(_BUCKET_MODELS, prefix)
        )

        resp = await download_links.mint_model_download_url(
            db, artifact.id, ArtifactFormat.LORA, ALICE
        )

        assert resp.truncated is True
        assert len(resp.files) == download_links.MAX_LISTING_OBJECTS

    async def test_409_when_format_never_exported(self, db: AsyncSession, patch_presign) -> None:
        _project, artifact = await _project_training_and_artifact(db, owner=ALICE.id)
        with pytest.raises(HTTPException) as exc:
            await download_links.mint_model_download_url(
                db, artifact.id, ArtifactFormat.GGUF, ALICE
            )
        assert exc.value.status_code == 409
