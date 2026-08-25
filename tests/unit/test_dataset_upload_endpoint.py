"""G4 — `POST /api/v1/datasets/upload` (ready-to-train dataset upload).

Sibling surface to `upload-seed` (see `tests/unit/test_format_detection_usage.py`
for that one's harness, which this file mirrors): a dataset that is already
trainable, as opposed to seed rows meant to be expanded via SDG. Exercises
the real `datasets_service.upload_dataset` entrypoint end to end against an
in-memory aiosqlite DB + `fake_minio`, with `OPENROUTER_API_KEY` left at its
empty default so Format Detection takes the deterministic
`passthrough_with_required_check` path (no LLM call needed to prove any of
these cases).

Covers the acceptance list:
  * happy path canonical JSONL: source == "uploaded", status == "completed",
    storage_uri is `s3://...` and contains `uploads/`, num_samples correct
  * JSON-array input (not just JSONL)
  * empty file -> 400
  * oversize file -> 413
  * all-rows-invalid (missing required keys) -> 400
  * semantic-guard (classification labels that read like free text) -> 422
  * project/upload task_type mismatch -> 400
  * `.pdf` upload -> 400 (PDF is seed-only)
  * the audit row for a successful upload has action == "dataset.upload"
"""

from __future__ import annotations

import json
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from api.models.audit_event import AuditEvent
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.project import Project
from api.schemas.enums import DatasetSource, JobStatus, TaskType
from api.services import datasets_service as ds_module


# ---- KNOWN GOTCHA shim: JSONB is Postgres-only, sqlite needs a stand-in ----
# (see tests/unit/test_dataset_status.py, which this mirrors)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


@pytest.fixture
async def async_session():
    """In-memory aiosqlite engine + AsyncSession with the full ORM schema."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def qa_project(async_session: AsyncSession) -> Project:
    proj = Project(id=uuid4(), name="qa-proj", task_type=TaskType.QA, owner_id="owner-1")
    async_session.add(proj)
    await async_session.flush()
    return proj


@pytest.fixture
async def classification_project(async_session: AsyncSession) -> Project:
    proj = Project(
        id=uuid4(), name="cls-proj", task_type=TaskType.CLASSIFICATION, owner_id="owner-1"
    )
    async_session.add(proj)
    await async_session.flush()
    return proj


def _jsonl_file(rows: list[dict], filename: str = "data.jsonl") -> UploadFile:
    body = "\n".join(json.dumps(r) for r in rows).encode("utf-8")
    return UploadFile(file=BytesIO(body), filename=filename)


def _json_array_file(rows: list[dict], filename: str = "data.json") -> UploadFile:
    body = json.dumps(rows).encode("utf-8")
    return UploadFile(file=BytesIO(body), filename=filename)


async def _audit_rows(async_session: AsyncSession) -> list[AuditEvent]:
    result = await async_session.execute(select(AuditEvent))
    return list(result.scalars().all())


class TestHappyPathJsonl:
    async def test_canonical_jsonl_persists_uploaded_dataset(
        self, async_session: AsyncSession, qa_project: Project, fake_minio, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ds_module, "get_minio_client", lambda: fake_minio)

        rows = [
            {"question": "What is the return window?", "answer": "30 days"},
            {"question": "Do you ship internationally?", "answer": "Yes"},
        ]
        file = _jsonl_file(rows)

        response = await ds_module.upload_dataset(
            async_session,
            project_id=qa_project.id,
            task_type=TaskType.QA,
            name="my-upload",
            file=file,
            user=None,
        )

        assert response.num_samples == 2
        assert response.invalid_rows == []

        result = await async_session.execute(select(Dataset).where(Dataset.id == response.dataset_id))
        dataset = result.scalar_one()

        assert dataset.source == DatasetSource.UPLOADED
        assert dataset.status == JobStatus.COMPLETED
        assert dataset.num_samples == 2
        assert dataset.owner_id == qa_project.owner_id
        assert dataset.storage_uri.startswith("s3://")
        assert "uploads/" in dataset.storage_uri
        assert dataset.name == "my-upload"

        audit_rows = await _audit_rows(async_session)
        assert len(audit_rows) == 1
        assert audit_rows[0].action == "dataset.upload"
        assert audit_rows[0].resource_id == str(dataset.id)

    async def test_default_name_uses_upload_prefix(
        self, async_session: AsyncSession, qa_project: Project, fake_minio, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ds_module, "get_minio_client", lambda: fake_minio)

        file = _jsonl_file([{"question": "Q1", "answer": "A1"}])

        response = await ds_module.upload_dataset(
            async_session,
            project_id=qa_project.id,
            task_type=TaskType.QA,
            name=None,
            file=file,
            user=None,
        )

        result = await async_session.execute(select(Dataset).where(Dataset.id == response.dataset_id))
        dataset = result.scalar_one()
        assert dataset.name.startswith("upload-")


class TestHappyPathJsonArray:
    async def test_json_array_input_persists_dataset(
        self, async_session: AsyncSession, qa_project: Project, fake_minio, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ds_module, "get_minio_client", lambda: fake_minio)

        rows = [
            {"question": "What is the return window?", "answer": "30 days"},
            {"question": "Do you ship internationally?", "answer": "Yes"},
            {"question": "What payment methods?", "answer": "Card and PayPal"},
        ]
        file = _json_array_file(rows)

        response = await ds_module.upload_dataset(
            async_session,
            project_id=qa_project.id,
            task_type=TaskType.QA,
            name="array-upload",
            file=file,
            user=None,
        )

        assert response.num_samples == 3
        result = await async_session.execute(select(Dataset).where(Dataset.id == response.dataset_id))
        dataset = result.scalar_one()
        assert dataset.source == DatasetSource.UPLOADED
        assert dataset.num_samples == 3


class TestEmptyFile:
    async def test_empty_file_returns_400(
        self, async_session: AsyncSession, qa_project: Project, fake_minio, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ds_module, "get_minio_client", lambda: fake_minio)

        file = UploadFile(file=BytesIO(b"   "), filename="empty.jsonl")

        with pytest.raises(HTTPException) as exc_info:
            await ds_module.upload_dataset(
                async_session,
                project_id=qa_project.id,
                task_type=TaskType.QA,
                name=None,
                file=file,
                user=None,
            )
        assert exc_info.value.status_code == 400


class TestOversizeFile:
    async def test_oversize_file_returns_413(
        self, async_session: AsyncSession, qa_project: Project, fake_minio, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ds_module, "get_minio_client", lambda: fake_minio)

        oversize = b"x" * (ds_module._MAX_SEED_BYTES + 1)
        file = UploadFile(file=BytesIO(oversize), filename="huge.jsonl")

        with pytest.raises(HTTPException) as exc_info:
            await ds_module.upload_dataset(
                async_session,
                project_id=qa_project.id,
                task_type=TaskType.QA,
                name=None,
                file=file,
                user=None,
            )
        assert exc_info.value.status_code == 413


class TestAllRowsInvalid:
    async def test_all_rows_missing_required_keys_returns_400(
        self, async_session: AsyncSession, qa_project: Project, fake_minio, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ds_module, "get_minio_client", lambda: fake_minio)

        # OPENROUTER_API_KEY is empty by default in test settings, so Format
        # Detection takes the `passthrough_with_required_check` path: rows
        # missing required keys are dropped outright, with no LLM call.
        rows = [{"foo": "bar"}, {"baz": "qux"}]
        file = _jsonl_file(rows)

        with pytest.raises(HTTPException) as exc_info:
            await ds_module.upload_dataset(
                async_session,
                project_id=qa_project.id,
                task_type=TaskType.QA,
                name=None,
                file=file,
                user=None,
            )
        assert exc_info.value.status_code == 400


class TestSemanticGuard:
    async def test_classification_free_text_labels_returns_422(
        self,
        async_session: AsyncSession,
        classification_project: Project,
        fake_minio,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(ds_module, "get_minio_client", lambda: fake_minio)

        # Structurally valid classification rows, but the label reads like a
        # QA answer (long free text) rather than a short category name —
        # this is exactly the semantic-guard trip case.
        rows = [
            {
                "text": "I can't log into my account",
                "label": "This is a very long sentence pretending to be a label",
            },
        ]
        file = _jsonl_file(rows)

        with pytest.raises(HTTPException) as exc_info:
            await ds_module.upload_dataset(
                async_session,
                project_id=classification_project.id,
                task_type=TaskType.CLASSIFICATION,
                name=None,
                file=file,
                user=None,
            )
        assert exc_info.value.status_code == 422


class TestTaskTypeMismatch:
    async def test_task_type_mismatch_returns_400(
        self, async_session: AsyncSession, qa_project: Project, fake_minio, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ds_module, "get_minio_client", lambda: fake_minio)

        file = _jsonl_file([{"text": "hello", "label": "greeting"}])

        with pytest.raises(HTTPException) as exc_info:
            await ds_module.upload_dataset(
                async_session,
                project_id=qa_project.id,
                task_type=TaskType.CLASSIFICATION,
                name=None,
                file=file,
                user=None,
            )
        assert exc_info.value.status_code == 400


class TestPdfRejected:
    async def test_pdf_upload_returns_400(
        self, async_session: AsyncSession, qa_project: Project, fake_minio, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ds_module, "get_minio_client", lambda: fake_minio)

        file = UploadFile(file=BytesIO(b"%PDF-1.4 fake pdf bytes"), filename="seed.pdf")

        with pytest.raises(HTTPException) as exc_info:
            await ds_module.upload_dataset(
                async_session,
                project_id=qa_project.id,
                task_type=TaskType.QA,
                name=None,
                file=file,
                user=None,
            )
        assert exc_info.value.status_code == 400
