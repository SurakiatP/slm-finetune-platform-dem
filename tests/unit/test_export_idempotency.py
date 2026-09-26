"""`POST /{model_id}/export` gets the same dedupe window as the other three
submit endpoints (SDG generate / training start / evaluation start) — see
`api/services/idempotency.py` and `tests/unit/test_idempotent_submit.py`.

Note: `tests/unit/test_idempotent_submit.py::test_export_uses_the_in_flight_guard_instead`
documents an OLDER decision to keep export out of the window and rely solely
on `model_service.submit_export_job`'s resource-state 409 ("already has an
export in flight"). That guard is unaffected by this change — it still fires
for two genuinely different requests targeting the SAME in-flight export —
this module only covers the exact-duplicate-click case the window is for,
which the 409 guard cannot: it fires from inside `submit_export_job`, so it
never sees a request that `idempotency.replay` already short-circuited.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from starlette.requests import Request

from api.core.auth import CurrentUser
from api.services import idempotency

USER_A = CurrentUser(id="user-a-sub", email="a@example.com")
USER_B = CurrentUser(id="user-b-sub", email="b@example.com")

BODY = {"format": "gguf", "quantization": "q4_k_m"}


def _export_request(model_id: str, headers: dict | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": f"/api/v1/models/{model_id}/export",
            "headers": raw,
            "client": ("10.0.0.9", 1234),
            "query_string": b"",
        }
    )


@pytest.fixture
def redis(monkeypatch):
    import fakeredis.aioredis

    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(idempotency, "get_redis_client", lambda: client)
    monkeypatch.setattr(client, "aclose", lambda: _noop())
    return client


async def _noop():
    return None


# =============================================================================
# 1. Unit level — `replay`/`remember` against an export-shaped path
# =============================================================================


class TestExportDedupeWindow:
    async def test_identical_export_replays_the_first_response(self, redis) -> None:
        model_id = str(uuid4())
        first_payload = {"artifact_id": model_id, "job_id": "celery-task-1"}
        req = _export_request(model_id)

        await idempotency.remember(req, USER_A, BODY, first_payload)
        replayed = await idempotency.replay(_export_request(model_id), USER_A, BODY)

        assert replayed is not None
        assert replayed.status_code == 202
        assert replayed.headers[idempotency.REPLAY_HEADER] == "true"
        assert json.loads(bytes(replayed.body)) == first_payload

    async def test_different_model_id_same_body_is_not_deduped(self, redis) -> None:
        """`model_id` is a path param, not part of the body — the dedupe key
        must still tell two models' exports apart. `request.url.path` already
        carries the concrete id, so this holds without folding `model_id`
        into the hashed body."""
        model_a, model_b = str(uuid4()), str(uuid4())
        await idempotency.remember(_export_request(model_a), USER_A, BODY, {"job_id": "x"})
        assert await idempotency.replay(_export_request(model_b), USER_A, BODY) is None

    async def test_different_user_same_export_is_not_deduped(self, redis) -> None:
        model_id = str(uuid4())
        await idempotency.remember(_export_request(model_id), USER_A, BODY, {"job_id": "x"})
        assert await idempotency.replay(_export_request(model_id), USER_B, BODY) is None

    async def test_redis_outage_degrades_to_no_dedupe(self, monkeypatch) -> None:
        """A broken Redis must never turn an export submit into a 500."""

        class _Broken:
            async def get(self, *a, **kw):
                raise ConnectionError("redis is down")

            async def set(self, *a, **kw):
                raise ConnectionError("redis is down")

            async def aclose(self):
                return None

        monkeypatch.setattr(idempotency, "get_redis_client", lambda: _Broken())
        model_id = str(uuid4())
        assert await idempotency.replay(_export_request(model_id), USER_A, BODY) is None
        # Must not raise either.
        await idempotency.remember(_export_request(model_id), USER_A, BODY, {"job_id": "x"})


# =============================================================================
# 2. Router level — a double-click enqueues exactly one Celery task
# =============================================================================


class TestExportRouterLevelDedupe:
    @pytest.fixture
    def app_client(self, monkeypatch, redis):
        from fastapi.testclient import TestClient

        from api.core.auth import require_user
        from api.core.database import get_db
        from api.main import app
        from api.routers import models as models_router
        from api.schemas.artifacts import ModelExportResponse
        from api.schemas.enums import ArtifactFormat, JobStatus

        submitted: list[dict] = []

        async def _fake_submit_export_job(db, *, model_id, request, user=None):
            submitted.append({"model_id": str(model_id), **request.model_dump(mode="json")})
            return ModelExportResponse(
                artifact_id=model_id,
                format=ArtifactFormat.GGUF,
                job_id=f"celery-task-{len(submitted)}",
                status=JobStatus.PENDING,
                websocket_url="/ws/jobs/x",
            )

        monkeypatch.setattr(models_router, "submit_export_job", _fake_submit_export_job)

        async def _fake_db():
            yield None

        app.dependency_overrides[get_db] = _fake_db
        app.dependency_overrides[require_user] = lambda: None  # anonymous, phase 1
        with TestClient(app) as client:
            yield client, submitted
        app.dependency_overrides.clear()

    def test_double_click_enqueues_one_export(self, app_client) -> None:
        client, submitted = app_client
        model_id = str(uuid4())
        headers = {"X-Forwarded-For": "203.0.113.7"}

        first = client.post(
            f"/api/v1/models/{model_id}/export", json=BODY, headers=headers
        )
        second = client.post(
            f"/api/v1/models/{model_id}/export", json=BODY, headers=headers
        )

        assert first.status_code == 202 and second.status_code == 202
        assert len(submitted) == 1, "the second click reached the service"
        assert first.json() == second.json()
        assert second.headers.get(idempotency.REPLAY_HEADER) == "true"
        assert idempotency.REPLAY_HEADER not in first.headers

    def test_different_model_ids_both_enqueue(self, app_client) -> None:
        client, submitted = app_client
        headers = {"X-Forwarded-For": "203.0.113.7"}

        client.post(f"/api/v1/models/{uuid4()}/export", json=BODY, headers=headers)
        client.post(f"/api/v1/models/{uuid4()}/export", json=BODY, headers=headers)

        assert len(submitted) == 2, "distinct models must not be deduped together"

    def test_idempotency_key_header_beats_the_body_hash(self, app_client) -> None:
        """Same header, same model, different body still replays — matching
        `trainings`/`evaluations`'s behaviour."""
        client, submitted = app_client
        model_id = str(uuid4())
        headers = {
            "X-Forwarded-For": "203.0.113.7",
            idempotency.IDEMPOTENCY_KEY_HEADER: "client-uuid-1",
        }

        first = client.post(
            f"/api/v1/models/{model_id}/export", json=BODY, headers=headers
        )
        second = client.post(
            f"/api/v1/models/{model_id}/export",
            json={"format": "safetensors"},
            headers=headers,
        )

        assert len(submitted) == 1
        assert second.headers.get(idempotency.REPLAY_HEADER) == "true"
        assert first.json() == second.json()
