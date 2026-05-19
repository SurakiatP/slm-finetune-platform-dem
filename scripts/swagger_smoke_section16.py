"""§16 Full lifecycle smoke driver — run on vast.ai against a live API.

Walks the 9-step happy path from SWAGGER_GUIDE.md §16:
  T1  GET  /health
  T2  POST /api/v1/projects                        (qa)
  T3  POST /api/v1/datasets/upload-seed            (multipart, 5 QA rows)
  T4  GET  /api/v1/datasets/{id}/preview
  T5  POST /api/v1/trainings                       (manual, 1B QA, 1 epoch)
  T6  poll GET /api/v1/trainings/{id}              (until status=completed)
  T6b GET  /api/v1/trainings/{id}/mlflow-url
  T7  GET  /api/v1/models?training_job_id={id}
  T8  POST /api/v1/models/{id}/export              (gguf q4_k_m)
  T9  poll GET /api/v1/models/{id}                 (until gguf_uri set)
  T10 POST /api/v1/inference/chat/completions      ("capital of France?")
  T11 GET  /api/v1/inference/models                (list ollama models)

WebSocket /ws/jobs/{job_id} is exercised in a background thread during T6 to
verify training_progress events stream. Count is logged; presence is enough.

Usage:
    python3 swagger_smoke_section16.py [--base-url http://localhost:8000]

Exit code 0 = all PASS, 1 = any FAIL. PASS/FAIL lines printed to stdout AND
appended to /tmp/logs/section16.log.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
import threading
import time
import urllib.parse
from typing import Any, Callable

import httpx

try:
    from websockets.sync.client import connect as ws_connect
except ImportError:
    ws_connect = None

LOG_PATH = "/tmp/logs/section16.log"
log = logging.getLogger("smoke16")

QA_SEED = [
    {"question": "What is the capital of France?", "answer": "Paris"},
    {"question": "What is the capital of Germany?", "answer": "Berlin"},
    {"question": "What is the capital of Japan?", "answer": "Tokyo"},
    {"question": "What is the capital of Italy?", "answer": "Rome"},
    {"question": "What is the capital of Spain?", "answer": "Madrid"},
]


class Recorder:
    def __init__(self) -> None:
        self.results: list[tuple[str, str, str]] = []  # (task, status, detail)

    def record(self, task: str, status: str, detail: str = "") -> None:
        line = f"[§16] {task} {status} {detail}".rstrip()
        print(line, flush=True)
        with open(LOG_PATH, "a") as fh:
            fh.write(line + "\n")
        self.results.append((task, status, detail))

    def summary(self) -> tuple[int, int]:
        passed = sum(1 for _, s, _ in self.results if s == "PASS")
        failed = sum(1 for _, s, _ in self.results if s == "FAIL")
        return passed, failed


def safe(rec: Recorder, task: str, fn: Callable[[], Any]) -> Any:
    """Run `fn`; on exception record FAIL with traceback and return None."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        rec.record(task, "FAIL", f"{type(exc).__name__}: {exc}")
        return None


def http(method: str, url: str, **kw: Any) -> httpx.Response:
    timeout = kw.pop("timeout", 60.0)
    with httpx.Client(timeout=timeout) as c:
        r = c.request(method, url, **kw)
        return r


# ---------- WebSocket collector ----------

def _ws_collector(ws_url: str, bucket: dict[str, Any]) -> None:
    if ws_connect is None:
        bucket["error"] = "websockets not installed"
        return
    bucket["events"] = []
    try:
        with ws_connect(ws_url, open_timeout=10, close_timeout=5) as ws:
            while not bucket.get("stop"):
                try:
                    msg = ws.recv(timeout=2.0)
                except TimeoutError:
                    continue
                except Exception as exc:
                    bucket.setdefault("recv_errors", []).append(f"{type(exc).__name__}: {exc}")
                    break
                try:
                    parsed = json.loads(msg)
                except Exception:
                    parsed = {"raw": str(msg)[:200]}
                bucket["events"].append(parsed)
                if parsed.get("type") in {"completed", "failed", "training_completed", "training_failed"}:
                    break
    except Exception as exc:  # noqa: BLE001
        bucket["error"] = f"{type(exc).__name__}: {exc}"


# ---------- Tasks ----------

def run(base_url: str) -> int:
    rec = Recorder()
    state: dict[str, Any] = {}

    # T1
    def t1() -> None:
        r = http("GET", f"{base_url}/health")
        assert r.status_code == 200, f"status={r.status_code}"
        body = r.json()
        assert body.get("status") == "ok", f"body={body}"
        rec.record("T1 health", "PASS", str(body))

    safe(rec, "T1 health", t1)

    # T2 create project
    def t2() -> None:
        r = http(
            "POST",
            f"{base_url}/api/v1/projects",
            json={"name": "section16-smoke", "task_type": "qa"},
        )
        assert r.status_code in (200, 201), f"status={r.status_code} body={r.text[:200]}"
        body = r.json()
        state["project_id"] = body["id"]
        rec.record("T2 create project", "PASS", f"project_id={body['id']}")

    safe(rec, "T2 create project", t2)

    # T3 upload seed (multipart JSONL)
    def t3() -> None:
        jsonl = "\n".join(json.dumps(row) for row in QA_SEED).encode()
        files = {"file": ("seed.jsonl", io.BytesIO(jsonl), "application/jsonl")}
        data = {"project_id": state["project_id"], "task_type": "qa"}
        r = http(
            "POST",
            f"{base_url}/api/v1/datasets/upload-seed",
            data=data,
            files=files,
        )
        assert r.status_code in (200, 201), f"status={r.status_code} body={r.text[:300]}"
        body = r.json()
        state["dataset_id"] = body["dataset_id"]
        fd = body.get("format_detection") or {}
        ran = fd.get("ran")
        rec.record(
            "T3 upload-seed",
            "PASS",
            f"dataset_id={body['dataset_id']} num_samples={body.get('num_samples')} fd.ran={ran}",
        )

    safe(rec, "T3 upload-seed", t3)

    # T4 preview
    def t4() -> None:
        r = http(
            "GET",
            f"{base_url}/api/v1/datasets/{state['dataset_id']}/preview?limit=3",
        )
        assert r.status_code == 200, f"status={r.status_code} body={r.text[:200]}"
        body = r.json()
        items = body.get("samples") or body.get("items") or []
        assert len(items) >= 1, f"no preview items: {body}"
        rec.record(
            "T4 preview",
            "PASS",
            f"samples={len(items)} total={body.get('total')}",
        )

    safe(rec, "T4 preview", t4)

    # T5 submit training
    def t5() -> None:
        payload = {
            "mode": "manual",
            "project_id": state["project_id"],
            "dataset_id": state["dataset_id"],
            "base_model": "unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
            "training_name": "section16-smoke",
            "manual_config": {
                "num_train_epochs": 1,
                "per_device_train_batch_size": 1,
                "gradient_accumulation_steps": 1,
                "learning_rate": 2e-4,
                "max_seq_length": 512,
                "lora": {"r": 8, "alpha": 16, "dropout": 0.05},
            },
        }
        r = http("POST", f"{base_url}/api/v1/trainings", json=payload, timeout=30.0)
        assert r.status_code == 202, f"status={r.status_code} body={r.text[:300]}"
        body = r.json()
        state["training_id"] = body["training_id"]
        state["job_id"] = body["job_id"]
        state["ws_url"] = body.get("websocket_url")
        rec.record(
            "T5 submit training",
            "PASS",
            f"training_id={body['training_id']} job_id={body['job_id']}",
        )

    safe(rec, "T5 submit training", t5)

    # WebSocket collector in background — API returns relative path, prepend host
    ws_bucket: dict[str, Any] = {}
    if state.get("ws_url"):
        raw_ws = state["ws_url"]
        if raw_ws.startswith("/"):
            base_no_proto = base_url.split("://", 1)[1]
            ws_url = f"ws://{base_no_proto}{raw_ws}"
        elif raw_ws.startswith("http://"):
            ws_url = "ws://" + raw_ws[len("http://"):]
        elif raw_ws.startswith("https://"):
            ws_url = "wss://" + raw_ws[len("https://"):]
        else:
            ws_url = raw_ws
        t = threading.Thread(target=_ws_collector, args=(ws_url, ws_bucket), daemon=True)
        t.start()

    # T6 poll training status
    def t6() -> None:
        if not state.get("training_id"):
            raise RuntimeError("no training_id (T5 failed)")
        deadline = time.time() + 600  # 10 min
        last_status = None
        while time.time() < deadline:
            r = http("GET", f"{base_url}/api/v1/trainings/{state['training_id']}")
            assert r.status_code == 200, f"status={r.status_code}"
            body = r.json()
            status = body.get("status")
            if status != last_status:
                print(f"   [poll] status={status}", flush=True)
                last_status = status
            if status in {"completed", "failed", "cancelled"}:
                state["training_status"] = status
                state["training_body"] = body
                if status != "completed":
                    err = body.get("error_message", "<none>")
                    raise RuntimeError(f"training {status}: {err[:300]}")
                rec.record(
                    "T6 training completed",
                    "PASS",
                    f"mlflow_run_id={body.get('mlflow_run_id')} ended_at={body.get('ended_at')}",
                )
                return
            time.sleep(8)
        raise TimeoutError("training did not complete within 10 min")

    safe(rec, "T6 training completed", t6)

    # Stop WS collector
    ws_bucket["stop"] = True
    time.sleep(1.0)

    def t6b() -> None:
        n_events = len(ws_bucket.get("events", []))
        types = {ev.get("type") for ev in ws_bucket.get("events", [])} - {None}
        if "error" in ws_bucket:
            rec.record(
                "T6b WS progress",
                "FAIL",
                f"events={n_events} error={ws_bucket['error']}",
            )
            return
        if n_events < 1:
            rec.record("T6b WS progress", "FAIL", f"events=0 (no progress msgs)")
            return
        rec.record("T6b WS progress", "PASS", f"events={n_events} types={sorted(types)}")

    safe(rec, "T6b WS progress", t6b)

    # T6c mlflow URL
    def t6c() -> None:
        r = http(
            "GET",
            f"{base_url}/api/v1/trainings/{state['training_id']}/mlflow-url",
        )
        assert r.status_code == 200, f"status={r.status_code}"
        body = r.json()
        url = body.get("mlflow_url")
        assert url, f"no mlflow_url in {body}"
        rec.record("T6c mlflow-url", "PASS", url[:100])

    safe(rec, "T6c mlflow-url", t6c)

    # T7 list models
    def t7() -> None:
        r = http(
            "GET",
            f"{base_url}/api/v1/models",
            params={"training_job_id": state["training_id"]},
        )
        assert r.status_code == 200, f"status={r.status_code}"
        items = r.json().get("items") or []
        assert len(items) >= 1, f"no models for training: {r.text[:200]}"
        state["artifact_id"] = items[0]["id"]
        rec.record(
            "T7 list models",
            "PASS",
            f"artifact_id={items[0]['id']} size_mb={items[0].get('size_mb')}",
        )

    safe(rec, "T7 list models", t7)

    # T8 export GGUF
    def t8() -> None:
        if not state.get("artifact_id"):
            raise RuntimeError("no artifact_id (T7 failed)")
        r = http(
            "POST",
            f"{base_url}/api/v1/models/{state['artifact_id']}/export",
            json={"format": "gguf", "quantization": "q4_k_m"},
        )
        assert r.status_code == 202, f"status={r.status_code} body={r.text[:300]}"
        body = r.json()
        state["export_job_id"] = body.get("job_id")
        rec.record("T8 export submit", "PASS", f"job_id={body.get('job_id')}")

    safe(rec, "T8 export submit", t8)

    # T9 poll export
    def t9() -> None:
        if not state.get("artifact_id"):
            raise RuntimeError("no artifact_id")
        deadline = time.time() + 360  # 6 min
        while time.time() < deadline:
            r = http("GET", f"{base_url}/api/v1/models/{state['artifact_id']}")
            assert r.status_code == 200, f"status={r.status_code}"
            body = r.json()
            err = body.get("export_error_message")
            gguf = body.get("gguf_uri")
            ollama_tag = body.get("ollama_model_tag")
            if err:
                raise RuntimeError(f"export failed: {err[:300]}")
            if gguf:
                state["gguf_uri"] = gguf
                state["ollama_tag"] = ollama_tag
                rec.record(
                    "T9 export completed",
                    "PASS",
                    f"gguf_uri={gguf} ollama_tag={ollama_tag}",
                )
                return
            time.sleep(6)
        raise TimeoutError("export did not complete within 6 min")

    safe(rec, "T9 export completed", t9)

    # T10 chat completion
    def t10() -> None:
        if not state.get("artifact_id"):
            raise RuntimeError("no artifact_id")
        payload = {
            "model": state["artifact_id"],
            "messages": [
                {"role": "user", "content": "What is the capital of France? Answer in one word."}
            ],
            "max_tokens": 50,
            "temperature": 0.0,
        }
        r = http(
            "POST",
            f"{base_url}/api/v1/inference/chat/completions",
            json=payload,
            timeout=120.0,
        )
        assert r.status_code == 200, f"status={r.status_code} body={r.text[:400]}"
        body = r.json()
        choices = body.get("choices") or []
        assert choices, f"no choices: {body}"
        content = (choices[0].get("message") or {}).get("content", "")
        usage = body.get("usage") or {}
        rec.record(
            "T10 chat completion",
            "PASS",
            f"content={content[:80]!r} usage={usage}",
        )

    safe(rec, "T10 chat completion", t10)

    # T11 list ollama models
    def t11() -> None:
        r = http("GET", f"{base_url}/api/v1/inference/models")
        assert r.status_code == 200, f"status={r.status_code}"
        body = r.json()
        data = body.get("data") or []
        ids = [m.get("id") for m in data]
        assert ids, f"no models: {body}"
        rec.record("T11 inference models", "PASS", f"models={ids}")

    safe(rec, "T11 inference models", t11)

    passed, failed = rec.summary()
    print("=" * 60)
    print(f"§16 SUMMARY: {passed} PASS, {failed} FAIL")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://localhost:8000")
    args = p.parse_args()

    import os
    os.makedirs("/tmp/logs", exist_ok=True)
    # Truncate log
    open(LOG_PATH, "w").close()

    sys.exit(run(args.base_url))
