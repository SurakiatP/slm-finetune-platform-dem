"""SSE streaming for `/inference/chat/completions` and `/inference/completions` (T2).

Fakes Ollama by monkeypatching `inference_service._ollama_client` to hand back
an `httpx.AsyncClient(transport=httpx.MockTransport(...))` — the single seam
both the non-streaming (`_post_json`/`_get_json`/`_repin`) and streaming
(`_start_stream`) call paths go through. Stream bodies are faked as async
generators of raw SSE bytes (`httpx.Response(..., content=<async gen>)`),
which is exactly what `resp.aiter_lines()` needs.

`stream_slots.acquire`/`release` (T1, `api/services/stream_slots.py`) are
monkeypatched directly rather than exercised through fakeredis — this file
is about the streaming proxy's own behaviour (framing, timeouts, keep_alive,
audit, cancellation), not the limiter, which has its own test file
(`test_stream_slots.py`).

In-memory aiosqlite + monkeypatched Redis/Ollama only — no real Postgres,
Redis, or Ollama daemon.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from uuid import uuid4

import fakeredis
import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from api.core.auth import CurrentUser
from api.models.audit_event import AuditEvent
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.deployment import Deployment
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.api_keys import ApiKeyCreate
from api.schemas.enums import DatasetSource, JobStatus, TaskType, TrainingMode
from api.schemas.inference import ChatCompletionRequest, CompletionRequest
from api.services import api_keys_service, inference_service


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):
    return "JSON"


USER_A = CurrentUser(id="user-a-sub", email="a@example.com")
TAG_A = "user-a-sub/my-model"
TAG_BASE = "llama3.2:3b"


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


async def _chain(
    session: AsyncSession, owner_id: str, tag: str, *, deployed: JobStatus | None = None
) -> ModelArtifact:
    """Project -> Dataset -> TrainingJob -> ModelArtifact, optionally with a
    Deployment on top (`deployed=JobStatus.RUNNING` etc). Mirrors
    `test_inference_tenancy.py`'s `_chain` / `test_inference_api_key.py`'s
    `_deploy_chain`."""
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
    artifact = ModelArtifact(
        id=uuid4(), training_job_id=training.id, name=f"m-{owner_id}",
        base_model=training.base_model, lora_adapter_uri="s3://models/x",
        ollama_model_tag=tag,
    )
    session.add(artifact)
    await session.flush()
    if deployed is not None:
        deployment = Deployment(
            id=uuid4(), owner_id=owner_id, model_artifact_id=artifact.id,
            name=f"dep-{owner_id}", status=deployed, rate_limit_per_min=60,
        )
        session.add(deployment)
        await session.flush()
    await session.commit()
    return artifact


def _chat_chunk(tag: str, *, content: str = "", finish: str | None = None) -> dict:
    return {
        "id": "chatcmpl-x", "object": "chat.completion.chunk", "created": 0, "model": tag,
        "choices": [{"index": 0, "delta": {"content": content} if content else {},
                     "finish_reason": finish}],
    }


def _usage_chunk(tag: str, object_: str = "chat.completion.chunk") -> dict:
    return {
        "id": "chatcmpl-x", "object": object_, "created": 0, "model": tag, "choices": [],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }


def _sse_bytes(obj: dict | str) -> bytes:
    body = obj if isinstance(obj, str) else json.dumps(obj)
    return f"data: {body}\n\n".encode()


class FakeOllama:
    """Records every request `_ollama_client` sends and answers per-path."""

    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.requests: list[tuple[str, dict]] = []
        self.chat_stream_lines: list[bytes] = [
            _sse_bytes(_chat_chunk(tag, content="Hello")),
            _sse_bytes(_chat_chunk(tag, finish="stop")),
            _sse_bytes(_usage_chunk(tag)),
            _sse_bytes("[DONE]"),
        ]
        self.completions_stream_lines: list[bytes] = [
            _sse_bytes({"id": "cmpl-x", "object": "text_completion", "created": 0,
                        "model": tag, "choices": [{"index": 0, "text": "Hi", "finish_reason": None}]}),
            _sse_bytes("[DONE]"),
        ]
        self.error_status: int | None = None
        self.error_body: bytes = b"model not found"
        self.stream_delay: float = 0.0

    async def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content.decode()) if request.content else {}
        self.requests.append((path, body))

        if self.error_status is not None:
            return httpx.Response(self.error_status, content=self.error_body)

        if path == "/api/generate":
            return httpx.Response(200, json={"done": True})

        if not body.get("stream"):
            if path == "/v1/chat/completions":
                return httpx.Response(200, json={
                    "id": "chatcmpl-x", "created": 0, "model": self.tag,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                })
            if path == "/v1/completions":
                return httpx.Response(200, json={
                    "id": "cmpl-x", "created": 0, "model": self.tag,
                    "choices": [{"index": 0, "text": "hi", "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                })
            raise AssertionError(f"unexpected non-stream path {path}")

        lines = (
            self.chat_stream_lines if path == "/v1/chat/completions"
            else self.completions_stream_lines
        )
        delay = self.stream_delay

        async def gen() -> AsyncIterator[bytes]:
            for line in lines:
                if delay:
                    await asyncio.sleep(delay)
                yield line

        return httpx.Response(200, content=gen())


@pytest.fixture
def fake_ollama(monkeypatch):
    fake = FakeOllama(TAG_A)

    def _client_factory(timeout=None):
        return httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))

    monkeypatch.setattr(inference_service, "_ollama_client", _client_factory)
    return fake


@pytest.fixture
def fake_redis(monkeypatch):
    """Deterministic `_consume_rate_limit` backing store — real Redis
    connection errors would fail *open* (see `test_inference_api_key.py`),
    which is no good for a test that wants the limit to actually trip."""
    server = fakeredis.FakeServer()
    client = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    monkeypatch.setattr(inference_service, "get_redis_client", lambda: client)
    return client


@pytest.fixture
def fake_slots(monkeypatch):
    calls: dict = {"acquire": [], "release": []}

    async def _acquire(actor: str) -> str | None:
        calls["acquire"].append(actor)
        return "slot-1"

    async def _release(actor: str, slot_id: str | None) -> None:
        calls["release"].append((actor, slot_id))

    monkeypatch.setattr(inference_service.stream_slots, "acquire", _acquire)
    monkeypatch.setattr(inference_service.stream_slots, "release", _release)
    return calls


async def _consume(resp) -> list[bytes]:
    return [chunk async for chunk in resp.body_iterator]


async def _last_audit_row(db: AsyncSession, action: str) -> AuditEvent:
    rows = (
        await db.execute(
            select(AuditEvent).where(AuditEvent.action == action).order_by(AuditEvent.created_at.desc())
        )
    ).scalars().all()
    return rows[0]


# =============================================================================
# 1. Chat stream, happy path
# =============================================================================


class TestChatStreamHappyPath:
    async def test_stream_chunks_then_done_and_headers(self, db, fake_ollama, fake_slots) -> None:
        await _chain(db, USER_A.id, TAG_A)
        body = ChatCompletionRequest(
            model=TAG_A, messages=[{"role": "user", "content": "hi"}], stream=True
        )
        resp = await inference_service.chat_completions(
            db, body, USER_A, actor="user-a-sub"
        )
        assert resp.media_type == "text/event-stream"
        assert resp.headers["Cache-Control"] == "no-cache"
        assert resp.headers["X-Accel-Buffering"] == "no"

        chunks = await _consume(resp)
        assert chunks[-1] == b"data: [DONE]\n\n"
        texts = b"".join(chunks).decode()
        assert '"content": "Hello"' in texts or '"content":"Hello"' in texts

        path, sent = fake_ollama.requests[0]
        assert path == "/v1/chat/completions"
        assert sent["stream"] is True
        assert sent["stream_options"] == {"include_usage": True}

        assert fake_slots["acquire"] == ["user-a-sub"]
        assert fake_slots["release"] == [("user-a-sub", "slot-1")]

        row = await _last_audit_row(db, "inference.chat_completions")
        assert row.outcome == "completed"
        assert row.event_metadata["stream"] is True
        assert row.event_metadata["total_tokens"] == 5

    async def test_base_tag_no_keep_alive_platform_tag_any_owner_keep_alive(
        self, db, fake_ollama, fake_slots
    ) -> None:
        # Base tag: no DB lookup, never pinned.
        base_body = ChatCompletionRequest(
            model=TAG_BASE, messages=[{"role": "user", "content": "hi"}], stream=True
        )
        resp = await inference_service.chat_completions(db, base_body, None, actor="anon:1")
        await _consume(resp)
        assert "keep_alive" not in fake_ollama.requests[-1][1]

        # Platform tag owned by someone else, RUNNING: pin applies regardless
        # of who is asking (anonymous here) — the pin is daemon-global.
        await _chain(db, "someone-else", TAG_A, deployed=JobStatus.RUNNING)
        owned_body = ChatCompletionRequest(
            model=TAG_A, messages=[{"role": "user", "content": "hi"}], stream=True
        )
        resp = await inference_service.chat_completions(db, owned_body, None, actor="anon:1")
        await _consume(resp)
        assert fake_ollama.requests[-1][1]["keep_alive"] == -1


# =============================================================================
# 2. Non-stream keep_alive + bare-completions re-pin
# =============================================================================


class TestNonStreamKeepAlive:
    async def test_chat_keep_alive_only_when_running(self, db, fake_ollama, fake_slots) -> None:
        await _chain(db, USER_A.id, TAG_A)  # exported, no deployment
        body = ChatCompletionRequest(model=TAG_A, messages=[{"role": "user", "content": "hi"}])
        await inference_service.chat_completions(db, body, USER_A)
        assert "keep_alive" not in fake_ollama.requests[-1][1]

    async def test_bare_completions_repins_exactly_once_when_running(
        self, db, fake_ollama, fake_slots
    ) -> None:
        await _chain(db, USER_A.id, TAG_A, deployed=JobStatus.RUNNING)
        body = CompletionRequest(model=TAG_A, prompt="hi")
        await inference_service.text_completions(db, body, USER_A)
        assert "keep_alive" not in fake_ollama.requests[0][1]  # /v1/completions payload
        generate_calls = [r for r in fake_ollama.requests if r[0] == "/api/generate"]
        assert len(generate_calls) == 1
        assert generate_calls[0][1] == {"model": TAG_A, "keep_alive": -1, "stream": False}

    async def test_no_keep_alive_no_repin_without_running_deployment(
        self, db, fake_ollama, fake_slots
    ) -> None:
        await _chain(db, USER_A.id, TAG_A)  # exported, never deployed
        body = CompletionRequest(model=TAG_A, prompt="hi")
        await inference_service.text_completions(db, body, USER_A)
        assert not any(r[0] == "/api/generate" for r in fake_ollama.requests)
        assert "keep_alive" not in fake_ollama.requests[0][1]


# =============================================================================
# 3. Completions stream: chat-translated (saved system prompt) vs bare
# =============================================================================


class TestCompletionsStream:
    async def test_saved_system_prompt_streams_via_chat_and_translates(
        self, db, fake_ollama, fake_slots
    ) -> None:
        artifact = await _chain(db, USER_A.id, TAG_A)
        training = await db.get(TrainingJob, artifact.training_job_id)
        training.context_snapshot = {"system_prompt": "be terse"}
        await db.commit()

        body = CompletionRequest(model=TAG_A, prompt="hi", stream=True)
        resp = await inference_service.text_completions(db, body, USER_A)
        chunks = await _consume(resp)
        assert chunks[-1] == b"data: [DONE]\n\n"
        assert fake_ollama.requests[0][0] == "/v1/chat/completions"
        first = json.loads(chunks[0][len(b"data: "):])
        assert first["object"] == "text_completion"
        assert first["choices"][0]["text"] == "Hello"

    async def test_prompt_list_over_one_is_400_before_slot_acquire(
        self, db, fake_ollama, fake_slots
    ) -> None:
        body = CompletionRequest(model=TAG_A, prompt=["a", "b"], stream=True)
        with pytest.raises(HTTPException) as exc:
            await inference_service.text_completions(db, body, USER_A)
        assert exc.value.status_code == 400
        assert fake_slots["acquire"] == []


# =============================================================================
# 4. Pre-stream JSON errors
# =============================================================================


class TestPreStreamErrors:
    async def test_unknown_model_is_404_before_slot(self, db, fake_ollama, fake_slots) -> None:
        body = ChatCompletionRequest(
            model=str(uuid4()), messages=[{"role": "user", "content": "hi"}], stream=True
        )
        with pytest.raises(HTTPException) as exc:
            await inference_service.chat_completions(db, body, USER_A)
        assert exc.value.status_code == 404
        assert fake_slots["acquire"] == []

    async def test_slot_rejection_is_429_before_hitting_ollama(self, db, fake_ollama, monkeypatch) -> None:
        await _chain(db, USER_A.id, TAG_A)

        async def _acquire_429(actor: str) -> str | None:
            raise HTTPException(status_code=429, detail="too many", headers={"Retry-After": "5"})

        monkeypatch.setattr(inference_service.stream_slots, "acquire", _acquire_429)
        body = ChatCompletionRequest(
            model=TAG_A, messages=[{"role": "user", "content": "hi"}], stream=True
        )
        with pytest.raises(HTTPException) as exc:
            await inference_service.chat_completions(db, body, USER_A)
        assert exc.value.status_code == 429
        assert exc.value.headers["Retry-After"] == "5"
        assert fake_ollama.requests == []

    async def test_upstream_404_releases_slot_and_audits_error(
        self, db, fake_ollama, fake_slots
    ) -> None:
        await _chain(db, USER_A.id, TAG_A)
        fake_ollama.error_status = 404
        body = ChatCompletionRequest(
            model=TAG_A, messages=[{"role": "user", "content": "hi"}], stream=True
        )
        with pytest.raises(HTTPException) as exc:
            await inference_service.chat_completions(db, body, USER_A)
        assert exc.value.status_code == 404
        assert fake_slots["release"] == [("user-a-sub", "slot-1")]
        row = await _last_audit_row(db, "inference.chat_completions")
        assert row.outcome == "error"
        assert row.event_metadata["stream"] is True


# =============================================================================
# 5. Mid-stream errors and timeouts
# =============================================================================


class TestMidStreamFailures:
    async def test_bare_json_error_line_ends_stream_without_done(
        self, db, fake_ollama, fake_slots
    ) -> None:
        await _chain(db, USER_A.id, TAG_A)
        fake_ollama.chat_stream_lines = [
            _sse_bytes(_chat_chunk(TAG_A, content="partial")),
            b'{"error":"model crashed"}\n',
        ]
        body = ChatCompletionRequest(
            model=TAG_A, messages=[{"role": "user", "content": "hi"}], stream=True
        )
        resp = await inference_service.chat_completions(db, body, USER_A)
        chunks = await _consume(resp)
        assert b"[DONE]" not in chunks[-1]
        last = json.loads(chunks[-1][len(b"data: "):])
        assert last["error"]["type"] == "upstream_error"
        assert fake_slots["release"] == [("user-a-sub", "slot-1")]
        row = await _last_audit_row(db, "inference.chat_completions")
        assert row.outcome == "error"
        assert row.event_metadata["error_type"] == "upstream_error"

    async def test_idle_timeout_yields_timeout_event_and_releases_slot(
        self, db, fake_ollama, fake_slots, monkeypatch
    ) -> None:
        await _chain(db, USER_A.id, TAG_A)
        settings = inference_service.get_settings()
        monkeypatch.setattr(settings, "inference_stream_idle_timeout_seconds", 0.05)
        monkeypatch.setattr(settings, "inference_stream_max_seconds", 10)
        fake_ollama.chat_stream_lines = [_sse_bytes(_chat_chunk(TAG_A, content="hi"))]
        fake_ollama.stream_delay = 1.0  # never arrives within the idle window
        body = ChatCompletionRequest(
            model=TAG_A, messages=[{"role": "user", "content": "hi"}], stream=True
        )
        resp = await inference_service.chat_completions(db, body, USER_A)
        chunks = await _consume(resp)
        last = json.loads(chunks[-1][len(b"data: "):])
        assert last["error"]["type"] == "timeout"
        assert fake_slots["release"] == [("user-a-sub", "slot-1")]
        row = await _last_audit_row(db, "inference.chat_completions")
        assert row.outcome == "timeout"


# =============================================================================
# 6. Client disconnect at the generator level
# =============================================================================


class TestClientDisconnect:
    async def test_early_aclose_releases_slot_and_audits_disconnected(
        self, db, fake_ollama, fake_slots
    ) -> None:
        await _chain(db, USER_A.id, TAG_A)
        body = ChatCompletionRequest(
            model=TAG_A, messages=[{"role": "user", "content": "hi"}], stream=True
        )
        resp = await inference_service.chat_completions(db, body, USER_A)
        await resp.body_iterator.__anext__()
        await resp.body_iterator.aclose()

        assert fake_slots["release"] == [("user-a-sub", "slot-1")]
        row = await _last_audit_row(db, "inference.chat_completions")
        assert row.outcome == "client_disconnected"

    async def test_cancel_while_blocked_still_cleans_up(self, db, fake_ollama, fake_slots) -> None:
        await _chain(db, USER_A.id, TAG_A)
        fake_ollama.chat_stream_lines = [_sse_bytes(_chat_chunk(TAG_A, content="hi"))]
        fake_ollama.stream_delay = 5.0
        body = ChatCompletionRequest(
            model=TAG_A, messages=[{"role": "user", "content": "hi"}], stream=True
        )
        resp = await inference_service.chat_completions(db, body, USER_A)

        async def _consume_all():
            async for _ in resp.body_iterator:
                pass

        task = asyncio.ensure_future(_consume_all())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert fake_slots["release"] == [("user-a-sub", "slot-1")]
        row = await _last_audit_row(db, "inference.chat_completions")
        assert row.outcome == "client_disconnected"


# =============================================================================
# 7. Reviewer-requested coverage: key auth, rate limit, timeouts, malformed
#    upstream shapes, and pre-stream `db.commit()` failure cleanup.
# =============================================================================


class TestKeyAuthenticatedStream:
    async def test_key_stream_completes_pins_keep_alive_and_leaks_no_key(
        self, db, fake_ollama, fake_slots, caplog
    ) -> None:
        caplog.set_level(logging.DEBUG)
        artifact = await _chain(db, USER_A.id, TAG_A, deployed=JobStatus.RUNNING)
        deployment = (
            await db.execute(
                select(Deployment).where(Deployment.model_artifact_id == artifact.id)
            )
        ).scalar_one()
        created = await api_keys_service.create_key(db, ApiKeyCreate(name="k"), USER_A)

        body = ChatCompletionRequest(
            model=str(deployment.id), messages=[{"role": "user", "content": "hi"}], stream=True
        )
        resp = await inference_service.chat_completions(
            db, body, USER_A, api_key_id=str(created.id), actor="user-a-sub"
        )
        chunks = await _consume(resp)
        assert chunks[-1] == b"data: [DONE]\n\n"

        _path, sent = fake_ollama.requests[0]
        assert sent["keep_alive"] == -1

        row = await _last_audit_row(db, "inference.chat_completions")
        assert row.outcome == "completed"
        assert row.event_metadata["stream"] is True
        assert row.event_metadata["deployment_id"] == str(deployment.id)
        assert row.event_metadata["api_key_id"] == str(created.id)
        assert row.event_metadata["auth"] == "api_key"

        assert created.key not in json.dumps(row.event_metadata)
        assert created.key not in caplog.text


class TestDeploymentRateLimitStream:
    async def test_rate_limit_exceeded_is_429_before_slot_acquire(
        self, db, fake_ollama, fake_slots, fake_redis
    ) -> None:
        artifact = await _chain(db, USER_A.id, TAG_A, deployed=JobStatus.RUNNING)
        deployment = (
            await db.execute(
                select(Deployment).where(Deployment.model_artifact_id == artifact.id)
            )
        ).scalar_one()
        deployment.rate_limit_per_min = 1
        await db.commit()
        created = await api_keys_service.create_key(db, ApiKeyCreate(name="k"), USER_A)

        # Consume the single request/minute this deployment allows, so the
        # call under test is the one that trips the limit.
        await inference_service._consume_rate_limit(deployment)

        body = ChatCompletionRequest(
            model=str(deployment.id), messages=[{"role": "user", "content": "hi"}], stream=True
        )
        with pytest.raises(HTTPException) as exc:
            await inference_service.chat_completions(
                db, body, USER_A, api_key_id=str(created.id), actor="user-a-sub"
            )
        assert exc.value.status_code == 429
        assert fake_slots["acquire"] == []
        assert fake_ollama.requests == []


class TestTotalTimeout:
    async def test_total_timeout_yields_timeout_event_and_releases_slot(
        self, db, fake_ollama, fake_slots, monkeypatch
    ) -> None:
        await _chain(db, USER_A.id, TAG_A)
        settings = inference_service.get_settings()
        monkeypatch.setattr(settings, "inference_stream_idle_timeout_seconds", 10.0)
        monkeypatch.setattr(settings, "inference_stream_max_seconds", 0.1)
        # Chunks keep arriving (well within the idle window) but the total
        # wall-clock budget runs out first.
        fake_ollama.chat_stream_lines = [
            _sse_bytes(_chat_chunk(TAG_A, content=f"t{i}")) for i in range(20)
        ]
        fake_ollama.stream_delay = 0.03
        body = ChatCompletionRequest(
            model=TAG_A, messages=[{"role": "user", "content": "hi"}], stream=True
        )
        resp = await inference_service.chat_completions(db, body, USER_A, actor="user-a-sub")
        chunks = await _consume(resp)
        last = json.loads(chunks[-1][len(b"data: "):])
        assert last["error"]["type"] == "timeout"
        assert fake_slots["release"] == [("user-a-sub", "slot-1")]
        row = await _last_audit_row(db, "inference.chat_completions")
        assert row.outcome == "timeout"


class TestMidStreamErrorVariants:
    async def test_data_prefixed_error_event_ends_stream_without_done(
        self, db, fake_ollama, fake_slots
    ) -> None:
        await _chain(db, USER_A.id, TAG_A)
        fake_ollama.chat_stream_lines = [
            _sse_bytes(_chat_chunk(TAG_A, content="partial")),
            _sse_bytes({"error": {"message": "model crashed", "type": "server_error"}}),
        ]
        body = ChatCompletionRequest(
            model=TAG_A, messages=[{"role": "user", "content": "hi"}], stream=True
        )
        resp = await inference_service.chat_completions(db, body, USER_A, actor="user-a-sub")
        chunks = await _consume(resp)
        assert b"[DONE]" not in chunks[-1]
        last = json.loads(chunks[-1][len(b"data: "):])
        assert last["error"]["type"] == "upstream_error"
        assert last["error"]["message"] == "model crashed"
        row = await _last_audit_row(db, "inference.chat_completions")
        assert row.outcome == "error"
        assert row.event_metadata["error_type"] == "upstream_error"

    async def test_upstream_ends_without_done_is_upstream_error(
        self, db, fake_ollama, fake_slots
    ) -> None:
        await _chain(db, USER_A.id, TAG_A)
        fake_ollama.chat_stream_lines = [_sse_bytes(_chat_chunk(TAG_A, content="only one chunk"))]
        body = ChatCompletionRequest(
            model=TAG_A, messages=[{"role": "user", "content": "hi"}], stream=True
        )
        resp = await inference_service.chat_completions(db, body, USER_A, actor="user-a-sub")
        chunks = await _consume(resp)
        last = json.loads(chunks[-1][len(b"data: "):])
        assert last["error"]["type"] == "upstream_error"
        assert fake_slots["release"] == [("user-a-sub", "slot-1")]
        row = await _last_audit_row(db, "inference.chat_completions")
        assert row.outcome == "error"

    async def test_non_dict_chunk_ends_stream_with_upstream_error(
        self, db, fake_ollama, fake_slots
    ) -> None:
        await _chain(db, USER_A.id, TAG_A)
        fake_ollama.chat_stream_lines = [_sse_bytes([])]
        body = ChatCompletionRequest(
            model=TAG_A, messages=[{"role": "user", "content": "hi"}], stream=True
        )
        resp = await inference_service.chat_completions(db, body, USER_A, actor="user-a-sub")
        chunks = await _consume(resp)
        last = json.loads(chunks[-1][len(b"data: "):])
        assert last["error"]["type"] == "upstream_error"
        assert fake_slots["release"] == [("user-a-sub", "slot-1")]
        row = await _last_audit_row(db, "inference.chat_completions")
        assert row.outcome == "error"


class TestDbCommitFailureBeforeStreaming:
    async def test_commit_failure_propagates_and_cleans_up(
        self, db, fake_ollama, fake_slots, monkeypatch
    ) -> None:
        await _chain(db, USER_A.id, TAG_A)

        close_calls = {"client": 0, "upstream": 0}
        original_client_aclose = httpx.AsyncClient.aclose
        original_response_aclose = httpx.Response.aclose

        async def _tracked_client_aclose(self) -> None:
            close_calls["client"] += 1
            await original_client_aclose(self)

        async def _tracked_response_aclose(self) -> None:
            close_calls["upstream"] += 1
            await original_response_aclose(self)

        monkeypatch.setattr(httpx.AsyncClient, "aclose", _tracked_client_aclose)
        monkeypatch.setattr(httpx.Response, "aclose", _tracked_response_aclose)

        original_commit = db.commit
        calls = {"n": 0}

        async def _commit_once_then_ok():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("simulated commit failure")
            return await original_commit()

        monkeypatch.setattr(db, "commit", _commit_once_then_ok)

        body = ChatCompletionRequest(
            model=TAG_A, messages=[{"role": "user", "content": "hi"}], stream=True
        )
        with pytest.raises(RuntimeError, match="simulated commit failure"):
            await inference_service.chat_completions(db, body, USER_A, actor="user-a-sub")

        assert fake_slots["release"] == [("user-a-sub", "slot-1")]
        assert close_calls["client"] == 1
        assert close_calls["upstream"] == 1
