"""Regression — `DELETE /api/v1/datasets/{id}` honors the `ondelete=RESTRICT` FK.

Before the fix, deleting a dataset that had a `training_jobs` (or `evaluation_runs`)
row pointing at it raised SQLAlchemy `IntegrityError` (NotNullViolation), which
the global handler surfaced as HTTP 500. The intended behavior — given that
both FKs are declared `ondelete="RESTRICT"` + `nullable=False` — is HTTP 409
Conflict with `code: "conflict"`, telling the caller they must remove the
dependents (or delete the parent project) before retrying.
"""

from __future__ import annotations

import json
import os
import uuid

import httpx
import pytest

API_BASE = os.environ.get("INTEGRATION_API_URL", "http://localhost:8000")

pytestmark = pytest.mark.integration


def _stack_is_up() -> bool:
    try:
        return httpx.get(f"{API_BASE}/health", timeout=2.0).status_code == 200
    except Exception:  # noqa: BLE001
        return False


if not _stack_is_up():  # pragma: no cover — skipped at collection
    pytest.skip(
        f"compose stack not up at {API_BASE}; "
        "run `docker compose up -d` before pytest -m integration",
        allow_module_level=True,
    )


@pytest.fixture
def client() -> httpx.Client:
    with httpx.Client(base_url=API_BASE, timeout=30.0) as c:
        yield c


def _create_project_and_seed(client: httpx.Client) -> tuple[str, str]:
    """Create a fresh QA project + upload-seed dataset; return (project_id, dataset_id)."""
    suffix = uuid.uuid4().hex[:8]
    r = client.post(
        "/api/v1/projects",
        json={"name": f"del-regress-{suffix}", "task_type": "qa"},
    )
    assert r.status_code == 201, r.text
    project_id = r.json()["id"]

    seed_rows = [
        {"question": f"Q{i}?", "answer": f"A{i}"} for i in range(1, 6)
    ]
    body = "\n".join(json.dumps(r) for r in seed_rows).encode("utf-8")
    r = client.post(
        "/api/v1/datasets/upload-seed",
        data={"project_id": project_id, "task_type": "qa"},
        files={"file": ("seed.jsonl", body, "application/x-ndjson")},
    )
    assert r.status_code == 201, r.text
    return project_id, r.json()["dataset_id"]


def test_delete_standalone_dataset_succeeds(client: httpx.Client) -> None:
    """Sanity: a dataset with NO trainings/evals deletes cleanly (204)."""
    project_id, dataset_id = _create_project_and_seed(client)

    r = client.delete(f"/api/v1/datasets/{dataset_id}")
    assert r.status_code == 204, r.text

    # Cleanup project
    client.delete(f"/api/v1/projects/{project_id}")


def test_delete_dataset_with_training_returns_409(client: httpx.Client) -> None:
    """Regression: dataset referenced by a training_job → 409 conflict, not 500."""
    project_id, dataset_id = _create_project_and_seed(client)

    # Submit a training that references this dataset (no GPU needed — we only
    # want the row in `training_jobs` with the FK set; the job stays pending).
    r = client.post(
        "/api/v1/trainings",
        json={
            "mode": "manual",
            "project_id": project_id,
            "dataset_id": dataset_id,
            "base_model": "unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
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

    try:
        # The bug: this used to return 500 (NotNullViolation propagated).
        r = client.delete(f"/api/v1/datasets/{dataset_id}")
        assert r.status_code == 409, r.text
        body = r.json()
        assert body.get("code") == "conflict", body
        # Detail should mention WHY the delete was refused (helps the caller).
        detail = body.get("detail", "").lower()
        assert "training" in detail or "referenc" in detail, body

        # Dataset row must still exist after the failed delete.
        r = client.get(f"/api/v1/datasets/{dataset_id}")
        assert r.status_code == 200, r.text

        # And once the project is dropped (cascade), the dataset goes too.
        r = client.delete(f"/api/v1/projects/{project_id}")
        assert r.status_code == 204, r.text
        r = client.get(f"/api/v1/datasets/{dataset_id}")
        assert r.status_code == 404, r.text
    finally:
        # Belt-and-braces in case an assertion fired before cascade-delete ran.
        client.delete(f"/api/v1/trainings/{training_id}")
        client.delete(f"/api/v1/projects/{project_id}")
