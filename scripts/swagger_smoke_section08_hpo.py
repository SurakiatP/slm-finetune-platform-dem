"""§8 HPO smoke driver — submit HPO training, watch trials, verify best.

Smoke-tests Phase 6 / Swagger §8 mode=hpo end-to-end:
  T1  POST /api/v1/projects                         qa
  T2  POST /api/v1/datasets/upload-seed             5 QA rows
  T3  POST /api/v1/trainings (mode=hpo, n_trials=2) submit
  T4  poll GET /api/v1/trainings/{id}               until completed (~5 min)
  T5  GET  /api/v1/trainings/{id}                   verify best_metric_value + best_params_json
  T6  GET  /api/v1/trainings/{id}/mlflow-url        parent + nested runs visible
  T7  GET  /api/v1/models?training_job_id={id}      best-retrain artifact persisted

Usage:
    python3 swagger_smoke_section08_hpo.py [--base-url http://localhost:8000]
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

LOG_PATH = "/tmp/logs/section08.log"

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
        line = f"[§8] {task} {status} {detail}".rstrip()
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

    # T1 project
    def t1() -> None:
        r = http(
            "POST",
            f"{base_url}/api/v1/projects",
            json={"name": "section8-hpo-smoke", "task_type": "qa"},
        )
        assert r.status_code in (200, 201), f"status={r.status_code}"
        state["project_id"] = r.json()["id"]
        rec.record("T1 create project", "PASS", f"project_id={state['project_id']}")

    safe(rec, "T1 create project", t1)

    # T2 upload seed
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
        assert r.status_code in (200, 201), f"status={r.status_code} body={r.text[:200]}"
        body = r.json()
        state["dataset_id"] = body["dataset_id"]
        rec.record("T2 upload seed", "PASS", f"dataset_id={body['dataset_id']}")

    safe(rec, "T2 upload seed", t2)

    # T3 submit HPO
    def t3() -> None:
        if not state.get("dataset_id"):
            raise RuntimeError("no dataset_id (T2 failed)")
        payload = {
            "mode": "hpo",
            "project_id": state["project_id"],
            "dataset_id": state["dataset_id"],
            "base_model": "unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
            "training_name": "section8-hpo-smoke",
            "hpo_config": {
                "n_trials": 2,
                "objective_metric": "eval_loss",
                "direction": "minimize",
                "sampler": "tpe",
                "pruner": "median",
                "timeout_seconds": 600,
                "search_space": {
                    "learning_rate": {
                        "type": "float", "low": 1e-5, "high": 5e-4, "log": True
                    },
                    "lora_r": {"type": "categorical", "choices": [8, 16]},
                },
                "fixed_config": {
                    "num_train_epochs": 1,
                    "per_device_train_batch_size": 1,
                    "gradient_accumulation_steps": 1,
                    "max_seq_length": 512,
                },
            },
        }
        r = http("POST", f"{base_url}/api/v1/trainings", json=payload, timeout=30.0)
        assert r.status_code == 202, f"status={r.status_code} body={r.text[:400]}"
        body = r.json()
        state["training_id"] = body["training_id"]
        state["job_id"] = body["job_id"]
        rec.record(
            "T3 submit HPO",
            "PASS",
            f"training_id={body['training_id']} job_id={body['job_id']}",
        )

    safe(rec, "T3 submit HPO", t3)

    # T4 poll until completed (HPO can take several minutes)
    def t4() -> None:
        if not state.get("training_id"):
            raise RuntimeError("no training_id (T3 failed)")
        deadline = time.time() + 900  # 15 min cap
        last = None
        while time.time() < deadline:
            r = http("GET", f"{base_url}/api/v1/trainings/{state['training_id']}")
            assert r.status_code == 200, f"status={r.status_code}"
            body = r.json()
            status = body.get("status")
            if status != last:
                print(f"   [poll] status={status}", flush=True)
                last = status
            if status in {"completed", "failed", "cancelled"}:
                state["training_body"] = body
                if status != "completed":
                    err = body.get("error_message", "<none>")
                    raise RuntimeError(f"HPO {status}: {err[:400]}")
                rec.record(
                    "T4 HPO completed",
                    "PASS",
                    f"ended_at={body.get('ended_at')}",
                )
                return
            time.sleep(10)
        raise TimeoutError("HPO did not complete within 15 min")

    safe(rec, "T4 HPO completed", t4)

    # T5 verify best params + metric
    def t5() -> None:
        body = state.get("training_body")
        if not body:
            raise RuntimeError("no training_body")
        best_metric = body.get("best_metric_value")
        best_params = body.get("best_params_json")
        assert best_metric is not None, f"best_metric_value is None: {body}"
        assert best_params, f"best_params_json empty: {body}"
        rec.record(
            "T5 best params",
            "PASS",
            f"best_metric={best_metric} best_params={best_params}",
        )

    safe(rec, "T5 best params", t5)

    # T6 mlflow url
    def t6() -> None:
        r = http(
            "GET",
            f"{base_url}/api/v1/trainings/{state['training_id']}/mlflow-url",
        )
        assert r.status_code == 200, f"status={r.status_code}"
        body = r.json()
        url = body.get("mlflow_url")
        assert url, f"no mlflow_url: {body}"
        rec.record("T6 mlflow-url", "PASS", url[:100])

    safe(rec, "T6 mlflow-url", t6)

    # T7 best-retrain artifact persisted
    def t7() -> None:
        r = http(
            "GET",
            f"{base_url}/api/v1/models",
            params={"training_job_id": state["training_id"]},
        )
        assert r.status_code == 200, f"status={r.status_code}"
        items = r.json().get("items") or []
        assert len(items) >= 1, f"no artifact for HPO training: {r.text[:300]}"
        state["artifact_id"] = items[0]["id"]
        rec.record(
            "T7 best artifact",
            "PASS",
            f"artifact_id={items[0]['id']} size_mb={items[0].get('size_mb')}",
        )

    safe(rec, "T7 best artifact", t7)

    passed, failed = rec.summary()
    print("=" * 60)
    print(f"§8 HPO SUMMARY: {passed} PASS, {failed} FAIL")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://localhost:8000")
    args = p.parse_args()
    os.makedirs("/tmp/logs", exist_ok=True)
    open(LOG_PATH, "w").close()
    sys.exit(run(args.base_url))
