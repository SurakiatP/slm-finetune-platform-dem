"""End-to-end client example for the SLM Fine-Tuning Platform.

Walks the full lifecycle for one task type (defaults to QA):
  1. Create a project
  2. Generate synthetic data via SDG
  3. Submit a training job
  4. Stream WebSocket progress
  5. Export the resulting model to GGUF (registers with Ollama)
  6. Run an evaluation
  7. Hit the OpenAI-compatible inference endpoint

Run against a live stack:
    python examples/python_client.py --task-type qa
    python examples/python_client.py --task-type classification --num-samples 200

Environment variables:
    SLM_BASE_URL  default http://localhost:8000
    OPENROUTER_API_KEY (required for SDG + LLM judge)

Dependencies: `httpx`, `websockets` — both already in the project's base deps.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

import httpx
import websockets

DEFAULT_BASE_URL = "http://localhost:8000"

QA_SEED = [
    {"question": "What's your return window?", "answer": "30 days from purchase."},
    {"question": "Do I need a receipt?", "answer": "Yes, please keep your receipt."},
    {"question": "Can I return sale items?", "answer": "Sale items are final."},
    {"question": "How long for a refund?", "answer": "5–7 business days."},
    {"question": "Where to ship returns?", "answer": "Returns Lane 123, Springfield."},
]

CLASSIFICATION_LABELS = ["billing", "technical", "general"]
TOOL_DEFINITIONS = [
    {
        "name": "set_oven",
        "description": "Set oven temperature",
        "parameters": {"celsius": {"type": "integer", "required": True}},
    },
    {
        "name": "wait",
        "description": "Wait for N seconds",
        "parameters": {"seconds": {"type": "integer", "required": True}},
    },
]


# ---- Helpers ---------------------------------------------------------------


def _post(client: httpx.Client, path: str, body: dict, *, expected: int = 200) -> dict[str, Any]:
    resp = client.post(path, json=body)
    assert resp.status_code == expected, f"{path} failed: {resp.status_code} {resp.text}"
    return resp.json()


def _get(client: httpx.Client, path: str) -> dict[str, Any]:
    resp = client.get(path)
    resp.raise_for_status()
    return resp.json()


async def _stream_progress(ws_url: str, *, timeout: float = 1800.0) -> dict[str, Any]:
    """Subscribe to the per-job WebSocket; return the terminal message."""
    async with websockets.connect(ws_url, ping_interval=20) as ws:
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            raw = await ws.recv()
            msg = json.loads(raw)
            print("  ws:", msg.get("type"), {
                k: v for k, v in msg.items()
                if k not in {"type", "job_id", "timestamp"}
            })
            if msg.get("type") in {"completed", "failed"}:
                return msg
        raise TimeoutError(f"no terminal message within {timeout}s")


def _poll_dataset_until_ready(client: httpx.Client, dataset_id: str, *, timeout: float = 300.0) -> dict[str, Any]:
    """Fallback: poll the dataset row until it has rows; useful when WS is hard to reach."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ds = _get(client, f"/api/v1/datasets/{dataset_id}")
        if ds.get("storage_uri") and (ds.get("num_samples") or 0) > 0:
            return ds
        time.sleep(2.0)
    raise TimeoutError("dataset never became ready")


# ---- Per-task SDG body -----------------------------------------------------


def _sdg_body(project_id: str, task_type: str, num_samples: int) -> dict:
    if task_type == "qa":
        return {
            "sdg_mode": "with_seed",
            "project_id": project_id,
            "task_type": "qa",
            "task_description": "Answer questions about our return policy",
            "num_samples": num_samples,
            "seed_data": QA_SEED,
        }
    if task_type == "classification":
        return {
            "sdg_mode": "description_only",
            "project_id": project_id,
            "task_type": "classification",
            "task_description": "Classify customer support tickets",
            "num_samples": num_samples,
            "classification_config": {"labels": CLASSIFICATION_LABELS},
        }
    if task_type == "tool_calling":
        return {
            "sdg_mode": "description_only",
            "project_id": project_id,
            "task_type": "tool_calling",
            "task_description": "Translate cooking instructions to JSON tool calls",
            "num_samples": num_samples,
            "tool_calling_config": {"tool_definitions": TOOL_DEFINITIONS},
        }
    raise ValueError(f"unsupported task_type: {task_type}")


# ---- Main flow -------------------------------------------------------------


def run(*, base_url: str, task_type: str, num_samples: int, do_train: bool) -> None:
    print(f"== SLM Platform walkthrough — task_type={task_type} ==")
    with httpx.Client(base_url=base_url, timeout=60.0) as client:
        # 1. Create project
        proj = _post(
            client,
            "/api/v1/projects",
            {
                "name": f"example-{task_type}",
                "description": f"end-to-end {task_type} demo",
                "task_type": task_type,
            },
            expected=201,
        )
        project_id = proj["id"]
        print(f"[1] created project {project_id}")

        # 2. Submit SDG
        sdg = _post(
            client,
            "/api/v1/datasets/generate",
            _sdg_body(project_id, task_type, num_samples),
            expected=202,
        )
        dataset_id = sdg["dataset_id"]
        sdg_job_id = sdg["job_id"]
        print(f"[2] enqueued SDG dataset={dataset_id} job={sdg_job_id}")

        # 3. Stream progress (or fall back to polling)
        try:
            asyncio.run(_stream_progress(_ws_url(base_url, sdg_job_id), timeout=600.0))
        except Exception as exc:  # noqa: BLE001
            print(f"  ws stream failed ({exc}); falling back to dataset polling")
            _poll_dataset_until_ready(client, dataset_id, timeout=600.0)

        ds = _get(client, f"/api/v1/datasets/{dataset_id}")
        print(f"[3] SDG done; {ds['num_samples']} samples at {ds.get('storage_uri')}")

        if not do_train:
            print("== skipping training (use --train to enable) ==")
            return

        # 4. Submit training (manual, small)
        tr = _post(
            client,
            "/api/v1/trainings",
            {
                "mode": "manual",
                "project_id": project_id,
                "dataset_id": dataset_id,
                "manual_config": {
                    "learning_rate": 2e-4,
                    "num_train_epochs": 1,
                    "per_device_train_batch_size": 1,
                    "gradient_accumulation_steps": 4,
                },
            },
            expected=202,
        )
        training_id = tr["training_id"]
        train_job_id = tr["job_id"]
        print(f"[4] enqueued training {training_id} job={train_job_id}")

        try:
            asyncio.run(_stream_progress(_ws_url(base_url, train_job_id), timeout=3600.0))
        except Exception as exc:  # noqa: BLE001
            print(f"  ws stream failed ({exc}); polling instead")
            while True:
                row = _get(client, f"/api/v1/trainings/{training_id}")
                if row["status"] in {"completed", "failed", "cancelled"}:
                    break
                time.sleep(5.0)

        row = _get(client, f"/api/v1/trainings/{training_id}")
        print(f"[5] training {row['status']}; mlflow_run_id={row.get('mlflow_run_id')}")
        if row["status"] != "completed":
            print(f"  error: {row.get('error_message')}")
            sys.exit(1)

        # 5. Find the resulting artifact
        artifacts = _get(client, f"/api/v1/models?project_id={project_id}")
        if not artifacts["items"]:
            print("[!] no model artifact found")
            return
        model_id = artifacts["items"][0]["id"]
        print(f"[6] artifact id={model_id}")

        # 6. Export to GGUF + register with Ollama
        exp = _post(
            client,
            f"/api/v1/models/{model_id}/export",
            {"format": "gguf", "quantization": "q4_k_m"},
            expected=202,
        )
        export_job_id = exp["job_id"]
        print(f"[7] export enqueued, job={export_job_id}")
        try:
            asyncio.run(_stream_progress(_ws_url(base_url, export_job_id), timeout=1800.0))
        except Exception as exc:  # noqa: BLE001
            print(f"  ws stream failed ({exc})")

        # 7. Hit /v1/chat/completions
        artifact_now = _get(client, f"/api/v1/models/{model_id}")
        if not artifact_now.get("ollama_model_tag"):
            print("[!] artifact missing ollama_model_tag; skipping inference")
            return

        completion = _post(
            client,
            "/api/v1/inference/chat/completions",
            {
                "model": model_id,  # service resolves UUID to ollama_model_tag
                "messages": [{"role": "user", "content": "Test question?"}],
                "temperature": 0.0,
                "stream": False,
            },
        )
        msg = completion["choices"][0]["message"]["content"]
        print(f"[8] inference: {msg!r}")


def _ws_url(base_url: str, job_id: str) -> str:
    return base_url.replace("http://", "ws://").replace("https://", "wss://") + f"/ws/jobs/{job_id}"


# ---- CLI -------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="API base URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--task-type",
        choices=["qa", "classification", "tool_calling"],
        default="qa",
    )
    parser.add_argument("--num-samples", type=int, default=20)
    parser.add_argument(
        "--train",
        action="store_true",
        help="Also submit a training job (requires GPU worker)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run(
        base_url=args.base_url,
        task_type=args.task_type,
        num_samples=args.num_samples,
        do_train=args.train,
    )
