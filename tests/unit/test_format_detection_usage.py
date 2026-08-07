"""T12 — proves seed-upload Format Detection usage is persisted correctly.

Format Detection is the one counted OpenRouter surface that runs in the
API process (not a Celery worker) — `datasets_service._upload_jsonl_seed`
calls it synchronously (via `asyncio.to_thread`) during `upload_seed_dataset`.
This module proves the three cases spelled out in the T12 acceptance
criteria, driving the real `upload_seed_dataset` service entrypoint end to
end against an in-memory aiosqlite DB + `fake_minio`, with a fake
`OpenRouterClient` swapped in at the exact use-site
(`datasets_service.OpenRouterClient`) so no real network call is made:

  1. Already-canonical rows -> Format Detection skipped -> no usage row.
  2. Non-canonical rows + a successful LLM mapping -> exactly one usage row,
     `stage="format_detection"`, model = `ai_engine.data_gen.models.FORMAT_DETECTION`.
  3. Non-canonical rows + an LLM error -> no usage row (the upload itself
     also fails with 400, since the fallback drops every row that never
     got renamed — but the acceptance criterion is specifically "no usage
     row", which we assert directly against the table).

Everything here runs against in-memory fakes only, same harness as
`tests/unit/test_dataset_status.py` (which this file's DB fixture mirrors).
"""

from __future__ import annotations

from io import BytesIO
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from ai_engine.data_gen import models as llm_models
from ai_engine.data_gen.openrouter_client import ChatResult
from ai_engine.data_gen.usage import STAGE_FORMAT_DETECTION
from api.core.config import get_settings
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.project import Project
from api.models.usage_event import UsageEvent
from api.schemas.enums import TaskType


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
async def project(async_session: AsyncSession) -> Project:
    proj = Project(id=uuid4(), name="proj", task_type=TaskType.QA)
    async_session.add(proj)
    await async_session.flush()
    return proj


class _FakeORClient:
    """Minimal stand-in for OpenRouterClient, swapped in at the exact
    `datasets_service.OpenRouterClient` use-site (module-local import
    alias — patching the source module's attribute afterwards wouldn't
    reach it, same gotcha called out for `get_minio_client` in
    test_dataset_status.py).
    """

    def __init__(
        self,
        *,
        response_content: str | None = None,
        raise_exc: Exception | None = None,
        model: str = llm_models.FORMAT_DETECTION,
        prompt_tokens: int = 37,
        completion_tokens: int = 11,
    ) -> None:
        self.response_content = response_content
        self.raise_exc = raise_exc
        self.model = model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens

    def chat(self, **kwargs: Any) -> ChatResult:
        if self.raise_exc is not None:
            raise self.raise_exc
        return ChatResult(
            content=self.response_content,
            model=self.model,
            finish_reason="stop",
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
        )


def _seed_upload_file(rows: list[dict]) -> UploadFile:
    """Build a fake JSONL UploadFile from row dicts."""
    import json

    body = "\n".join(json.dumps(r) for r in rows).encode("utf-8")
    return UploadFile(file=BytesIO(body), filename="seed.jsonl")


async def _usage_rows(async_session: AsyncSession) -> list[UsageEvent]:
    result = await async_session.execute(select(UsageEvent))
    return list(result.scalars().all())


class TestFormatDetectionSkippedWritesNoUsageRow:
    async def test_canonical_upload_writes_no_usage_row(
        self, async_session: AsyncSession, project: Project, fake_minio, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from api.services import datasets_service as ds_module

        monkeypatch.setattr(ds_module, "get_minio_client", lambda: fake_minio)

        # A non-empty key is needed here so the code takes the
        # `detect_and_rename` branch (rather than datasets_service's own
        # "no API key configured" passthrough, which always reports
        # ran=True regardless of whether the rows are canonical) — the
        # thing under test is `already_canonical`'s short-circuit *inside*
        # detect_and_rename. `OpenRouterClient(...)` is still constructed
        # unconditionally by `_run_format_detection` (that's just object
        # construction), but `.chat()` must never be called for
        # already-canonical rows — fail loudly if that assumption breaks.
        settings = get_settings()
        monkeypatch.setattr(settings, "openrouter_api_key", "test-key")

        def _chat_must_not_be_called(**kwargs: Any) -> ChatResult:
            raise AssertionError("client.chat() must not be called for canonical rows")

        fake_client = _FakeORClient()
        fake_client.chat = _chat_must_not_be_called  # type: ignore[method-assign]
        monkeypatch.setattr(ds_module, "OpenRouterClient", lambda **kwargs: fake_client)

        file = _seed_upload_file([{"question": "What is the return window?", "answer": "30 days"}])

        response = await ds_module.upload_seed_dataset(
            async_session,
            project_id=project.id,
            task_type=TaskType.QA,
            name="canonical-seed",
            file=file,
            user=None,
        )

        assert response.num_samples == 1
        assert response.format_detection.ran is False

        rows = await _usage_rows(async_session)
        assert rows == []


class TestFormatDetectionSuccessWritesOneUsageRow:
    async def test_llm_triggered_upload_writes_exactly_one_row(
        self, async_session: AsyncSession, project: Project, fake_minio, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from api.services import datasets_service as ds_module

        monkeypatch.setattr(ds_module, "get_minio_client", lambda: fake_minio)

        settings = get_settings()
        monkeypatch.setattr(settings, "openrouter_api_key", "test-key")

        fake_client = _FakeORClient(
            response_content='{"field_mapping": {"q": "question", "a": "answer"}}',
        )
        monkeypatch.setattr(ds_module, "OpenRouterClient", lambda **kwargs: fake_client)

        # Non-canonical keys force Format Detection to actually run.
        file = _seed_upload_file([{"q": "What is the return window?", "a": "30 days"}])

        response = await ds_module.upload_seed_dataset(
            async_session,
            project_id=project.id,
            task_type=TaskType.QA,
            name="mapped-seed",
            file=file,
            user=None,
        )

        assert response.num_samples == 1
        assert response.format_detection.ran is True

        rows = await _usage_rows(async_session)
        assert len(rows) == 1
        row = rows[0]
        assert row.stage == STAGE_FORMAT_DETECTION
        assert row.model == llm_models.FORMAT_DETECTION
        assert row.provider == "openrouter"
        assert row.outcome == "completed"
        assert row.job_id is None
        assert row.project_id == project.id
        assert row.prompt_tokens == 37
        assert row.completion_tokens == 11


class TestFormatDetectionErrorWritesNoUsageRow:
    async def test_llm_error_upload_writes_no_usage_row(
        self, async_session: AsyncSession, project: Project, fake_minio, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from api.services import datasets_service as ds_module

        monkeypatch.setattr(ds_module, "get_minio_client", lambda: fake_minio)

        settings = get_settings()
        monkeypatch.setattr(settings, "openrouter_api_key", "test-key")

        fake_client = _FakeORClient(raise_exc=RuntimeError("network died"))
        monkeypatch.setattr(ds_module, "OpenRouterClient", lambda **kwargs: fake_client)

        # Non-canonical keys force Format Detection to actually run; the
        # LLM errors, so the passthrough fallback can't rename them, they
        # stay missing the required "question"/"answer" keys, and every
        # row gets dropped -> upload_seed_dataset raises 400. That's
        # expected and is not what this test is about: the point is that,
        # error or not, no usage row is ever written for a call that was
        # never billed.
        file = _seed_upload_file([{"q": "What is the return window?", "a": "30 days"}])

        with pytest.raises(HTTPException) as excinfo:
            await ds_module.upload_seed_dataset(
                async_session,
                project_id=project.id,
                task_type=TaskType.QA,
                name="error-seed",
                file=file,
                user=None,
            )
        assert excinfo.value.status_code == 400

        rows = await _usage_rows(async_session)
        assert rows == []
