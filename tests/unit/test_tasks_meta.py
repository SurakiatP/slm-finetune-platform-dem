"""Unit tests for the static metadata endpoints.

Focus: `GET /api/v1/sdg-pipeline`, which exposes the SDG pipeline's fixed
model config live so the frontend can fetch it instead of hard-coding a
mirror that goes stale.

Pure unit test — the endpoint touches no DB and makes no network calls, so
we drive it through a plain `fastapi.testclient.TestClient(app)`. No unit
test in this suite establishes an HTTP-client fixture to reuse, so we build
one locally. The expected body is derived from the `ai_engine.data_gen.models`
constants themselves, so this test can never itself go stale.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from ai_engine.data_gen import models as llm_models
from api.main import app

client = TestClient(app)


def test_sdg_pipeline_returns_live_model_config() -> None:
    resp = client.get("/api/v1/sdg-pipeline")

    assert resp.status_code == 200

    body = resp.json()
    assert body == {
        "generator": llm_models.GENERATOR,
        "judge": llm_models.JUDGE,
        "diversity_rules": llm_models.DIVERSITY_RULES,
    }


def test_sdg_pipeline_exposes_exactly_the_three_fields() -> None:
    body = client.get("/api/v1/sdg-pipeline").json()

    assert set(body.keys()) == {"generator", "judge", "diversity_rules"}
    assert all(isinstance(v, str) for v in body.values())
