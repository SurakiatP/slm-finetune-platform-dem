"""Shared pytest fixtures for the SLM Fine-Tuning Platform test suite.

Centralizes the mocked-external primitives used by Tier 2 of the snapshot /
characterization-test harness. See `docs/runbooks/snapshot_harness.md` for the
full workflow.

Available fixtures (all session-scoped factories where it helps):

  • ``openrouter_responder``    — install a programmable fake on the
    OpenRouterClient / AsyncOpenRouterClient instance the caller provides.
    Promoted from ``tests/unit/test_async_openrouter_client.py``'s
    ``_FakeCompletions`` so it is reusable across files.

  • ``recorded_payload``        — load a recorded JSON response from
    ``tests/fixtures/recorded/openrouter/<name>.json``. Skips the test with
    a clear instruction if the fixture isn't on disk yet.

  • ``fake_minio``              — in-memory MinIO stub (put_object / get_object
    / fget_object / fput_object / stat_object). Patches
    ``workers.storage.get_minio_client`` to return it for the duration of the
    test.

  • ``fake_redis_pubsub``       — fakeredis Server + Redis client; publishes
    are observable via ``fake_redis_pubsub.published`` for assertion.

  • ``seed_dataset_factory``    — builds tiny in-memory row sets per task type
    for use as SDG seed input.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

# Provide a benign DATABASE_URL so importing modules that touch
# `api.core.config.get_settings()` (e.g. anything via `workers.celery_app`)
# doesn't fail collection. Tests that need real DB access mark
# themselves @pytest.mark.integration and skip when compose isn't up.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost:5432/test_unused",
)

import pytest


# ---- OpenRouter responder ---------------------------------------------------


class _FakeCompletions:
    """Programmable fake for ``openai.AsyncOpenAI.chat.completions.create``.

    Captures every call's kwargs into ``self.calls`` so tests can assert on
    the request payload after the run. The ``responder`` may be:

      • a static ``SimpleNamespace`` (returned for every call)
      • an ``async`` callable ``(kwargs, call_index) -> SimpleNamespace``
    """

    def __init__(self, responder: Any) -> None:
        self._responder = responder
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if callable(self._responder):
            return await self._responder(kwargs, len(self.calls) - 1)
        return self._responder


def _build_chat_response(content: str, model: str = "stub-model") -> Any:
    """Wrap a content string in the SDK's response shape."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason="stop",
            )
        ],
        model=model,
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


@pytest.fixture
def openrouter_responder():
    """Factory: ``install(client, responder) → fake_completions``.

    Returns the fake so the test can inspect ``.calls`` afterwards.
    """

    async def _noop_close() -> None:
        return None

    def _install(client: Any, responder: Any) -> _FakeCompletions:
        fake = _FakeCompletions(responder)
        client._client = SimpleNamespace(
            chat=SimpleNamespace(completions=fake),
            close=_noop_close,
        )
        return fake

    _install.build_response = _build_chat_response  # type: ignore[attr-defined]
    return _install


# ---- Recorded-payload loader -----------------------------------------------


_RECORDED_DIR = Path(__file__).parent / "fixtures" / "recorded" / "openrouter"


@pytest.fixture
def recorded_payload(request: pytest.FixtureRequest):
    """Load `tests/fixtures/recorded/openrouter/<name>.json` or skip cleanly.

    Usage::

        def test_sdg_qa(recorded_payload):
            payload = recorded_payload("sdg_qa_batch")  # → dict
            ...

    If the file doesn't exist, the test is skipped with the exact capture
    instruction so the next live-run session knows what to record.
    """

    def _load(name: str) -> dict[str, Any]:
        path = _RECORDED_DIR / f"{name}.json"
        if not path.exists():
            pytest.skip(
                f"recorded fixture not present: {path}\n"
                "Capture it during the next live SDG run (see "
                "docs/runbooks/snapshot_harness.md → §Live capture)."
            )
        return json.loads(path.read_text(encoding="utf-8"))

    return _load


# ---- In-memory MinIO stub ---------------------------------------------------


class _FakeMinio:
    """Tiny in-memory MinIO substitute.

    Supports the verbs we exercise in production worker code:
    ``put_object``, ``fput_object``, ``get_object``, ``fget_object``,
    ``stat_object``, ``bucket_exists``, ``make_bucket``, ``list_objects``,
    ``remove_object``. Returns SimpleNamespace shapes that match the real
    ``minio`` SDK closely enough for our callers.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], bytes] = {}
        self._buckets: set[str] = set()

    # bucket lifecycle
    def bucket_exists(self, bucket_name: str) -> bool:
        return bucket_name in self._buckets

    def make_bucket(self, bucket_name: str) -> None:
        self._buckets.add(bucket_name)

    # objects
    def put_object(
        self,
        bucket_name: str,
        object_name: str,
        data: Any,
        length: int = -1,
        **_: Any,
    ) -> Any:
        self._buckets.add(bucket_name)
        if hasattr(data, "read"):
            payload = data.read()
        elif isinstance(data, (bytes, bytearray)):
            payload = bytes(data)
        else:
            payload = str(data).encode("utf-8")
        self._store[(bucket_name, object_name)] = payload
        return SimpleNamespace(etag="fake-etag", object_name=object_name)

    def fput_object(
        self,
        bucket_name: str,
        object_name: str,
        file_path: str,
        **_: Any,
    ) -> Any:
        self._buckets.add(bucket_name)
        with open(file_path, "rb") as fh:
            self._store[(bucket_name, object_name)] = fh.read()
        return SimpleNamespace(etag="fake-etag", object_name=object_name)

    def get_object(self, bucket_name: str, object_name: str) -> Any:
        body = self._store.get((bucket_name, object_name))
        if body is None:
            raise KeyError(f"fake_minio: {bucket_name}/{object_name} not found")

        def _stream(chunk_size: int = 64 * 1024):
            for i in range(0, len(body), chunk_size):
                yield body[i : i + chunk_size]

        return SimpleNamespace(
            read=lambda *_a, **_k: body,
            stream=_stream,
            close=lambda: None,
            release_conn=lambda: None,
        )

    def fget_object(
        self,
        bucket_name: str,
        object_name: str,
        file_path: str,
        **_: Any,
    ) -> Any:
        body = self._store.get((bucket_name, object_name))
        if body is None:
            raise KeyError(f"fake_minio: {bucket_name}/{object_name} not found")
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        Path(file_path).write_bytes(body)
        return SimpleNamespace(etag="fake-etag", object_name=object_name)

    def stat_object(self, bucket_name: str, object_name: str) -> Any:
        body = self._store.get((bucket_name, object_name))
        if body is None:
            raise KeyError(f"fake_minio: {bucket_name}/{object_name} not found")
        return SimpleNamespace(size=len(body), etag="fake-etag")

    def list_objects(self, bucket_name: str, prefix: str = "", recursive: bool = False):
        for (b, k), _v in self._store.items():
            if b != bucket_name:
                continue
            if prefix and not k.startswith(prefix):
                continue
            yield SimpleNamespace(object_name=k, size=len(self._store[(b, k)]))

    def remove_object(self, bucket_name: str, object_name: str) -> None:
        self._store.pop((bucket_name, object_name), None)

    def presigned_get_object(
        self,
        bucket_name: str,
        object_name: str,
        expires: Any = None,
        response_headers: dict[str, str] | None = None,
        **_: Any,
    ) -> str:
        """Deterministic fake presigned URL — NOT a substitute for the
        real-`Minio` URL-shape test.

        This fake can only prove that `download_links.py` calls through to
        *some* presign function with the right bucket/key/expires — it
        cannot catch a wrong host, a missing `region=`, or a broken
        path-style vs virtual-style URL, because it never touches SigV4 at
        all. Those are exactly the bug classes that break on real
        hardware, which is why they get their own no-network test against
        a real `Minio` instance in `tests/unit/test_presign_url_shape.py`
        instead of being (wrongly) trusted to this fake.
        """
        query = f"X-Fake-Expires={expires}"
        if response_headers:
            for k, v in sorted(response_headers.items()):
                query += f"&{k}={v}"
        return f"https://fake-presigned.example.com/{bucket_name}/{object_name}?{query}"


@pytest.fixture
def fake_minio(monkeypatch: pytest.MonkeyPatch) -> _FakeMinio:
    """In-memory MinIO; patches both `get_minio_client` and
    `get_presign_client` (workers.storage) to return the *same* fake
    object, so existing byte-level assertions (built against
    `get_minio_client`) keep holding while presigned-URL code paths also
    get a working fake to call through to.
    """
    client = _FakeMinio()

    def _factory() -> _FakeMinio:
        return client

    # Patch at the module-of-definition; workers/tasks/* import via this name.
    import workers.storage

    monkeypatch.setattr(workers.storage, "get_minio_client", _factory)
    monkeypatch.setattr(workers.storage, "get_presign_client", _factory)
    return client


# ---- fakeredis Pub/Sub ------------------------------------------------------


@pytest.fixture
def fake_redis_pubsub(monkeypatch: pytest.MonkeyPatch):
    """fakeredis Server + Redis; capture published messages for assertion.

    Returns a SimpleNamespace with ``client`` (the Redis instance) and
    ``published`` (list of (channel, message) tuples).
    """
    import fakeredis

    server = fakeredis.FakeServer()
    redis_client = fakeredis.FakeStrictRedis(server=server, decode_responses=False)

    published: list[tuple[str, bytes]] = []
    original_publish = redis_client.publish

    def _spy_publish(channel: str, message: Any) -> int:
        if isinstance(message, str):
            message = message.encode("utf-8")
        published.append((channel, message))
        return original_publish(channel, message)

    redis_client.publish = _spy_publish  # type: ignore[assignment]
    return SimpleNamespace(
        server=server,
        client=redis_client,
        published=published,
    )


# ---- Seed-dataset factory ---------------------------------------------------


@pytest.fixture
def seed_dataset_factory() -> Callable[..., list[dict[str, Any]]]:
    """Return a builder for tiny task-typed seed rows.

    Usage::

        rows = seed_dataset_factory("classification", n=4)
    """
    from api.schemas.enums import TaskType

    def _build(
        task: str | TaskType,
        *,
        n: int = 3,
    ) -> list[dict[str, Any]]:
        task_type = TaskType(task) if isinstance(task, str) else task
        if task_type is TaskType.CLASSIFICATION:
            labels = ["ปัญหาเทคนิค", "ปัญหาการเงิน", "คำถามทั่วไป"]
            return [
                {"text": f"ตัวอย่างคำถาม #{i}", "label": labels[i % len(labels)]}
                for i in range(n)
            ]
        if task_type is TaskType.QA:
            return [
                {
                    "question": f"คำถามตัวอย่าง #{i}?",
                    "answer": f"คำตอบที่ {i}.",
                }
                for i in range(n)
            ]
        # tool_calling
        tools = ["set_volume", "play_music", "light_on"]
        return [
            {
                "question": f"สั่งงานสมาร์ทโฮม #{i}",
                "answer": json.dumps(
                    {"name": tools[i % len(tools)], "parameters": {"level": 50 + i}},
                    ensure_ascii=False,
                ),
            }
            for i in range(n)
        ]

    return _build


# ---- Counters helper (debug, for assertion ergonomics) ---------------------


@pytest.fixture
def call_counter() -> defaultdict:
    """``defaultdict[str, int]`` for ergonomic per-name call counting."""
    return defaultdict(int)
