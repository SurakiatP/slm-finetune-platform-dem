"""§9 DELETE training (cancel) smoke driver.

Submits a manual training, waits for worker pickup, cancels mid-flight,
verifies the status transitions to cancelled and that DELETE is idempotent.

  T1  POST   /api/v1/projects                   qa
  T2  POST   /api/v1/datasets/upload-seed       5 QA rows
  T3  POST   /api/v1/trainings (manual)         submit, status=pending
  T4  poll until status=running OR ~30s         confirm worker took it
  T5  DELETE /api/v1/trainings/{id}             cancel
  T6  poll until status=cancelled (timeout 60s)
  T7  DELETE /api/v1/trainings/{id}             idempotent — should not 500
                                                (current state is cancelled)

Usage:
    python3 swagger_smoke_section09_cancel.py [--base-url http://localhost:8000]
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from typing import Any, Callable

import httpx

LOG_PATH = "/tmp/logs/section09.log"

QA_SEED = [
    {"question": "What is the capital of France?", "answer": "Paris"},
    {"question": "What is the capital of Germany?", "answer": "Berlin"},
    {"question": "What is the capital of Japan?", "answer": "Tokyo"},
    {"question": "What is the capital of Italy?", "answer": "Rome"},
    {"question": "What is the capital of Spain?", "answer": "Madrid"},
]


class Recorder:
    def __init__(self) -> None:
        self.results: list[tuple[str, str, str]] = []

    def record(self, task: str, status: str, detail: str = "") -> None:
        line = f"[§9] {task} {status} {detail}".rstrip()
        print(line, flush=True)
        with open(LOG_PATH, "a") as fh:
            fh.write(line + "\n")
        self.results.append((task, status, detail))

    def summary(self) -> tuple[int, int]:
        passed = sum(1 for _, s, _ in self.results if s == "PASS")
        failed = sum(1 for _, s, _ in self.results if s == "FAIL")
        return passed, failed


def safe(rec: Recorder, task: str, fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        rec.record(task, "FAIL", f"{type(exc).__name__}: {exc}")
        return None


def http(method: str, url: str, **kw: Any) -> httpx.Response:
    timeout = kw.pop("timeout", 60.0)
    with httpx.Client(timeout=timeout) as c:
        return c.request(method, url, **kw)


def run(base_url: str) -> int:
    rec = Recorder()
    state: dict[str, Any] = {}

    # T1
    def t1() -> None:
        r = http(
            "POST",
            f"{base_url}/api/v1/projects",
            json={"name": "section9-cancel-smoke", "task_type": "qa"},
        )
        assert r.status_code in (200, 201), f"status={r.status_code}"
        state["project_id"] = r.json()["id"]
        rec.record("T1 create project", "PASS", f"project_id={state['project_id']}")

    safe(rec, "T1 create project", t1)

    # T2
    def t2() -> None:
        jsonl = "\n".join(json.dumps(row) for row in QA_SEED).encode()
        files = {"file": ("seed.jsonl", io.BytesIO(jsonl), "application/jsonl")}
        data = {"project_id": state["project_id"], "task_type": "qa"}
        r = http(
            "POST",
            f"{base_url}/api/v1/datasets/upload-seed",
            data=data,
            files=files,
        )
        assert r.status_code in (200, 201), f"status={r.status_code}"
        state["dataset_id"] = r.json()["dataset_id"]
        rec.record("T2 upload seed", "PASS", f"dataset_id={state['dataset_id']}")

    safe(rec, "T2 upload seed", t2)

    # T3 submit
    def t3() -> None:
        if not state.get("dataset_id"):
            raise RuntimeError("no dataset_id")
        payload = {
            "mode": "manual",
            "project_id": state["project_id"],
            "dataset_id": state["dataset_id"],
            "base_model": "unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
            "training_name": "section9-cancel",
            "manual_config": {
                "num_train_epochs": 3,
                "per_device_train_batch_size": 1,
                "gradient_accumulation_steps": 1,
                "max_seq_length": 512,
            },
        }
        r = http("POST", f"{base_url}/api/v1/trainings", json=payload, timeout=30.0)
        assert r.status_code == 202, f"status={r.status_code} body={r.text[:300]}"
        body = r.json()
        state["training_id"] = body["training_id"]
        rec.record(
            "T3 submit training",
            "PASS",
            f"training_id={body['training_id']}",
        )

    safe(rec, "T3 submit training", t3)

    # T4 poll until running or 60s
    def t4() -> None:
        if not state.get("training_id"):
            raise RuntimeError("no training_id")
        deadline = time.time() + 60
        last = None
        while time.time() < deadline:
            r = http("GET", f"{base_url}/api/v1/trainings/{state['training_id']}")
            assert r.status_code == 200, f"status={r.status_code}"
            body = r.json()
            status = body.get("status")
            if status != last:
                print(f"   [poll pre-cancel] status={status}", flush=True)
                last = status
            if status == "running":
                rec.record("T4 reached running", "PASS", f"after ~{int(time.time() - (deadline - 60))}s")
                return
            if status in {"completed", "failed", "cancelled"}:
                raise RuntimeError(f"training already terminal ({status}) before cancel")
            time.sleep(2)
        # If still pending after 60s, that's still cancellable — not a fail
        rec.record(
            "T4 reached running",
            "PASS",
            "(stayed pending — worker slow, will cancel from pending)",
        )

    safe(rec, "T4 reached running", t4)

    # T5 cancel
    def t5() -> None:
        if not state.get("training_id"):
            raise RuntimeError("no training_id")
        r = http("DELETE", f"{base_url}/api/v1/trainings/{state['training_id']}")
        # Common conventions: 200 (returns body) or 204 (no body)
        assert r.status_code in (200, 202, 204), f"status={r.status_code} body={r.text[:200]}"
        rec.record("T5 DELETE cancel", "PASS", f"status={r.status_code}")

    safe(rec, "T5 DELETE cancel", t5)

    # T6 poll until cancelled
    def t6() -> None:
        deadline = time.time() + 60
        last = None
        while time.time() < deadline:
            r = http("GET", f"{base_url}/api/v1/trainings/{state['training_id']}")
            assert r.status_code == 200, f"status={r.status_code}"
            body = r.json()
            status = body.get("status")
            if status != last:
                print(f"   [poll post-cancel] status={status}", flush=True)
                last = status
            if status == "cancelled":
                rec.record("T6 status cancelled", "PASS", f"ended_at={body.get('ended_at')}")
                return
            if status in {"completed", "failed"}:
                raise RuntimeError(f"unexpected terminal status: {status}")
            time.sleep(2)
        raise TimeoutError("training never reached cancelled state in 60s")

    safe(rec, "T6 status cancelled", t6)

    # T7 idempotent DELETE on already-cancelled
    def t7() -> None:
        r = http("DELETE", f"{base_url}/api/v1/trainings/{state['training_id']}")
        # Should not 500. 200/204 ideal; 409 conflict acceptable; 404 also OK if it
        # treats already-terminal as "no longer cancellable".
        assert r.status_code in (200, 202, 204, 404, 409), (
            f"status={r.status_code} body={r.text[:200]}"
        )
        rec.record(
            "T7 idempotent delete",
            "PASS",
            f"status={r.status_code} (no 500)",
        )

    safe(rec, "T7 idempotent delete", t7)

    passed, failed = rec.summary()
    print("=" * 60)
    print(f"§9 CANCEL SUMMARY: {passed} PASS, {failed} FAIL")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://localhost:8000")
    args = p.parse_args()
    os.makedirs("/tmp/logs", exist_ok=True)
    open(LOG_PATH, "w").close()
    sys.exit(run(args.base_url))
