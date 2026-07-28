"""End-to-end integration test — exercises the full API surface against a live stack.

Marked `@pytest.mark.integration`; runs only when the docker-compose services
are up. Probes Postgres + Redis at session start and skips the entire module
if either is unreachable.

Run with the compose stack:
    docker compose up -d
    pytest -m integration tests/integration/test_full_flow.py -v

The test always walks: project creation -> seed upload -> SDG submission ->
SDG completion (checked via both a WebSocket smoke check and authoritative
REST polling). When `INTEGRATION_HAS_GPU=1` is set, it additionally walks
the GPU-bound tail of the lifecycle: training -> loss-history readback ->
model listing -> GGUF export -> inference -> evaluation. Without a GPU
worker, the test stops right after the SDG stage (`pytest.skip`).

Run with the compose stack:
    docker compose up -d
    pytest -m integration tests/integration/test_full_flow.py -v

Run the full GPU-bound tail (on a box with a CUDA worker):
    INTEGRATION_HAS_GPU=1 pytest -m integration tests/integration/test_full_flow.py -v
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

import httpx
import pytest
import websockets

API_BASE = os.environ.get("INTEGRATION_API_URL", "http://localhost:8000")

pytestmark = pytest.mark.integration


# ---- Skip-if-stack-down --------------------------------------------------


def _stack_is_up() -> bool:
    try:
        r = httpx.get(f"{API_BASE}/health", timeout=2.0)
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def _has_gpu() -> bool:
    """True if the worker host has CUDA — used to skip GPU-bound steps."""
    return os.environ.get("INTEGRATION_HAS_GPU", "").lower() in {"1", "true", "yes"}


pytest.importorskip(
    "httpx",
    reason="httpx is required for integration tests",
)

if not _stack_is_up():  # pragma: no cover — skipped at collection
    pytest.skip(
        f"compose stack not up at {API_BASE}; "
        "run `docker compose up -d` before pytest -m integration",
        allow_module_level=True,
    )


# ---- Fixtures -------------------------------------------------------------


@pytest.fixture
def client() -> httpx.Client:
    with httpx.Client(base_url=API_BASE, timeout=30.0) as c:
        yield c


# ---- Helpers --------------------------------------------------------------


def _wait_for_status(
    client: httpx.Client,
    path: str,
    *,
    target: set[str],
    timeout_seconds: int = 120,
    poll_interval: float = 1.5,
) -> dict[str, Any]:
    """Poll `path` until `status` is in `target` or timeout fires."""
    deadline = time.monotonic() + timeout_seconds
    last_payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        resp = client.get(path)
        if resp.status_code != 200:
            time.sleep(poll_interval)
            continue
        last_payload = resp.json()
        if last_payload.get("status") in target:
            return last_payload
        time.sleep(poll_interval)
    raise AssertionError(
        f"timed out after {timeout_seconds}s; last status="
        f"{last_payload.get('status')!r} (target {target})"
    )


def _wait_for_export(
    client: httpx.Client,
    model_id: str,
    *,
    timeout_seconds: int = 900,
    poll_interval: float = 3.0,
) -> dict[str, Any]:
    """Poll `GET /models/{id}` until `gguf_uri` or `export_error_message` is set.

    Unlike Dataset / TrainingJob / EvaluationRun, ModelArtifact doesn't carry
    a generic `status` enum — export completion is inferred from these two
    mutually-exclusive-on-success fields instead, so this can't reuse
    `_wait_for_status`.
    """
    deadline = time.monotonic() + timeout_seconds
    last_payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        resp = client.get(f"/api/v1/models/{model_id}")
        if resp.status_code == 200:
            last_payload = resp.json()
            if last_payload.get("gguf_uri") or last_payload.get("export_error_message"):
                return last_payload
        time.sleep(poll_interval)
    raise AssertionError(
        f"export timed out after {timeout_seconds}s; last payload={last_payload}"
    )


def _ws_smoke_check(job_id: str, *, timeout_seconds: float = 5.0) -> None:
    """Confirm the job-progress WebSocket channel accepts a connection.

    Not a substitute for the authoritative REST poll — just proves the
    Redis pub/sub -> FastAPI -> client plumbing is alive. Receiving an
    actual message is a bonus, not required: for tiny jobs the SDG task may
    finish (and stop publishing) before we connect, so a timeout waiting for
    a message is treated as fine. A failure to *connect* at all is not
    swallowed — that's a real regression in the WS endpoint.
    """
    ws_base = API_BASE.replace("http://", "ws://").replace("https://", "wss://")

    async def _go() -> None:
        uri = f"{ws_base}/ws/jobs/{job_id}"
        async with websockets.connect(uri, open_timeout=timeout_seconds) as ws:
            try:
                await asyncio.wait_for(ws.recv(), timeout=timeout_seconds)
            except asyncio.TimeoutError:
                pass

    asyncio.run(_go())


# ---- Tests ----------------------------------------------------------------


def test_qa_full_flow(client: httpx.Client) -> None:
    """QA: project → seed upload → SDG (with_seed → seed_dataset_id) → train.

    Phase 9: the SDG submission references the uploaded seed by id rather
    than inlining the rows. Format Detection runs at upload time; the
    response body now includes a `format_detection` audit and an optional
    `pdf_uri` (None for JSONL uploads).
    """
    # 1. Create project
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "integration-qa",
            "description": "policy QA",
            "task_type": "qa",
        },
    )
    assert r.status_code == 201, r.text
    project_id = r.json()["id"]

    # 2. Upload seed (tiny but valid)
    seed_rows = [
        {"question": "What's your return window?", "answer": "30 days."},
        {"question": "Do I need a receipt?", "answer": "Yes, please keep it."},
        {"question": "Can I return sale items?", "answer": "Sale items are final."},
        {"question": "How long for refunds?", "answer": "5–7 business days."},
        {"question": "Where to ship returns?", "answer": "Returns Lane 123."},
    ]
    body = "\n".join(json.dumps(r) for r in seed_rows).encode("utf-8")
    r = client.post(
        "/api/v1/datasets/upload-seed",
        data={"project_id": project_id, "task_type": "qa", "name": "seed-v1"},
        files={"file": ("seed.jsonl", body, "application/x-ndjson")},
    )
    assert r.status_code == 201, r.text
    upload_payload = r.json()
    seed_dataset_id = upload_payload["dataset_id"]
    # Phase 9 contract: the response body always carries a FormatDetectionReport.
    assert "format_detection" in upload_payload
    assert "ran" in upload_payload["format_detection"]

    # 3. Verify preview returns the seed rows
    r = client.get(f"/api/v1/datasets/{seed_dataset_id}/preview?limit=3")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["task_type"] == "qa"
    assert len(body["samples"]) >= 1

    # 4. Submit SDG (small batch) — Phase 9 references the seed by id.
    r = client.post(
        "/api/v1/datasets/generate",
        json={
            "sdg_mode": "with_seed",
            "project_id": project_id,
            "task_type": "qa",
            "task_description": "Answer policy questions",
            "num_samples": 10,
            "seed_dataset_id": seed_dataset_id,
        },
    )
    assert r.status_code == 202, r.text
    sdg = r.json()
    sdg_dataset_id = sdg["dataset_id"]

    # 4b. WebSocket smoke check — confirm the job-progress channel is alive.
    _ws_smoke_check(sdg["job_id"])

    # 5. Wait for SDG completion via dataset status polling (proxy for the WS).
    final = _wait_for_status(
        client,
        f"/api/v1/datasets/{sdg_dataset_id}",
        target={"completed", "failed"},
        # 60s was too tight against a live OpenRouter round-trip (with_seed
        # QA, num_samples=10, incl. judge-filter pass) — observed ~75-90s on
        # a real run; 60s only ever passed against recorded/mocked latency.
        timeout_seconds=180,
    )
    # If the test infra doesn't have the OPENROUTER_API_KEY set, SDG fails fast;
    # this assertion surfaces that as a clean test failure via error_message.
    assert final.get("status") == "completed", final.get("error_message")
    assert final.get("storage_uri"), "SDG completed but storage_uri is empty"
    assert final.get("num_samples", 0) > 0

    # 6. Submit training (manual, tiny). Skipped without GPU.
    if not _has_gpu():
        pytest.skip("INTEGRATION_HAS_GPU not set; skipping GPU-bound train+eval")

    r = client.post(
        "/api/v1/trainings",
        json={
            "mode": "manual",
            "project_id": project_id,
            "dataset_id": sdg_dataset_id,
            "manual_config": {
                "learning_rate": 2e-4,
                "num_train_epochs": 1,
                "per_device_train_batch_size": 1,
                "gradient_accumulation_steps": 1,
            },
        },
    )
    assert r.status_code == 202, r.text
    training_id = r.json()["training_id"]

    final = _wait_for_status(
        client,
        f"/api/v1/trainings/{training_id}",
        target={"completed", "failed"},
        timeout_seconds=600,
    )
    assert final["status"] == "completed", final.get("error_message")

    # 7. Loss-history readback — always available once a run has an mlflow_run_id.
    r = client.get(f"/api/v1/trainings/{training_id}/loss-history")
    assert r.status_code == 200, r.text
    loss_body = r.json()
    assert loss_body["training_id"] == training_id
    assert isinstance(loss_body["train_loss"], list)
    assert isinstance(loss_body["eval_loss"], list)

    # 8. A ModelArtifact should now exist for this training job.
    r = client.get("/api/v1/models", params={"training_job_id": training_id})
    assert r.status_code == 200, r.text
    models_page = r.json()
    assert models_page["total"] >= 1, "expected a ModelArtifact for the completed training"
    model_id = models_page["items"][0]["id"]

    # 9. Export to GGUF, then poll until gguf_uri or export_error_message appears.
    r = client.post(
        f"/api/v1/models/{model_id}/export",
        json={"format": "gguf", "quantization": "q4_k_m"},
    )
    assert r.status_code == 202, r.text

    artifact = _wait_for_export(client, model_id, timeout_seconds=900)
    assert artifact.get("gguf_uri"), artifact.get("export_error_message")
    assert artifact.get("ollama_model_tag"), "expected export to register an Ollama tag"

    # 10. Inference against the freshly exported model (OpenAI-compatible).
    r = client.post(
        "/api/v1/inference/chat/completions",
        json={
            "model": model_id,
            "messages": [{"role": "user", "content": "What's your return window?"}],
            "temperature": 0.0,
        },
    )
    assert r.status_code == 200, r.text
    chat = r.json()
    assert chat["choices"][0]["message"]["content"]

    # 11. Evaluate the exported model against the SDG-generated (train) dataset.
    r = client.post(
        "/api/v1/evaluations",
        json={
            "model_artifact_id": model_id,
            "dataset_id": sdg_dataset_id,
            "use_llm_judge": False,
        },
    )
    assert r.status_code == 202, r.text
    eval_id = r.json()["evaluation_id"]

    eval_final = _wait_for_status(
        client,
        f"/api/v1/evaluations/{eval_id}",
        target={"completed", "failed"},
        timeout_seconds=300,
    )
    assert eval_final["status"] == "completed", eval_final.get("error_message")
    assert eval_final.get("metrics"), "expected a non-empty metrics dict"


def test_classification_create_only(client: httpx.Client) -> None:
    """Classification project lifecycle without GPU steps."""
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "integration-cls",
            "task_type": "classification",
        },
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    # description_only mode requires classification_config.labels
    r = client.post(
        "/api/v1/datasets/generate",
        json={
            "sdg_mode": "description_only",
            "project_id": pid,
            "task_type": "classification",
            "task_description": "Classify support tickets",
            "num_samples": 12,
            "classification_config": {"labels": ["billing", "tech", "general"]},
        },
    )
    assert r.status_code == 202, r.text


def test_404_for_missing_project(client: httpx.Client) -> None:
    """Exercise the global error handler — 404 returns ErrorResponse shape."""
    r = client.get("/api/v1/projects/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404
    body = r.json()
    assert "detail" in body
    assert body.get("code") in {"not_found", "http_404"}


# ---- Phase 9 contract assertions -----------------------------------------


def test_legacy_seed_data_field_rejected(client: httpx.Client) -> None:
    """Phase 9 dropped inline seed_data — old payloads must 422.

    Frontends still on the Phase 4 contract get a clear schema error
    instead of silent drift.
    """
    r = client.post(
        "/api/v1/projects",
        json={"name": "legacy-check", "task_type": "qa"},
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    r = client.post(
        "/api/v1/datasets/generate",
        json={
            "sdg_mode": "with_seed",
            "project_id": pid,
            "task_type": "qa",
            "task_description": "x" * 20,
            "num_samples": 5,
            "seed_data": [{"question": "q", "answer": "a"}],
        },
    )
    assert r.status_code == 422, r.text


def test_legacy_teacher_model_field_rejected(client: httpx.Client) -> None:
    """Phase 9 dropped teacher_model override — `extra=forbid` rejects it."""
    r = client.post(
        "/api/v1/projects",
        json={"name": "legacy-tm", "task_type": "classification"},
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    r = client.post(
        "/api/v1/datasets/generate",
        json={
            "sdg_mode": "description_only",
            "project_id": pid,
            "task_type": "classification",
            "task_description": "Classify support tickets",
            "num_samples": 5,
            "classification_config": {"labels": ["a", "b"]},
            "teacher_model": "openai/gpt-4o-mini",
        },
    )
    assert r.status_code == 422, r.text


def test_seed_dataset_id_required_for_with_seed(client: httpx.Client) -> None:
    """with_seed mode without seed_dataset_id must 422."""
    r = client.post(
        "/api/v1/projects",
        json={"name": "noid-check", "task_type": "qa"},
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    r = client.post(
        "/api/v1/datasets/generate",
        json={
            "sdg_mode": "with_seed",
            "project_id": pid,
            "task_type": "qa",
            "task_description": "Answer policy questions",
            "num_samples": 5,
        },
    )
    assert r.status_code == 422, r.text


def test_seed_dataset_id_must_exist(client: httpx.Client) -> None:
    """A valid-looking-but-nonexistent seed_dataset_id surfaces as 404."""
    r = client.post(
        "/api/v1/projects",
        json={"name": "missing-seed", "task_type": "qa"},
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    r = client.post(
        "/api/v1/datasets/generate",
        json={
            "sdg_mode": "with_seed",
            "project_id": pid,
            "task_type": "qa",
            "task_description": "Answer policy questions",
            "num_samples": 5,
            "seed_dataset_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert r.status_code == 404, r.text
