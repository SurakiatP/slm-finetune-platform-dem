"""Session 22 — Full system smoke on vast.ai.

Runs end-to-end:
  • 6 SDG datasets (3 task types × 2 modes, 100 rows each)
  • 27 trainings (9 models ≤2B × 3 task types) — uses `with_seed` dataset per task
  • 27 GGUF exports + Ollama auto-register
  • 27 inference smoke tests (one task-appropriate prompt each)

Design:
  • stdlib only (urllib + json) — no requests dep, no httpx
  • crash-safe: state.json persisted after every milestone, re-runnable
  • sequential: worker concurrency=1, so no benefit to parallel submission

State file: /tmp/e2e/state.json
Seeds: ./seed_data/{task}/{task}_canonical.jsonl (already present in repo)

Usage:
    python scripts/session22_e2e_full_system.py \
        --base-url http://localhost:8000 \
        --state-dir /tmp/e2e

Re-run picks up wherever it left off (every step checks state.json first).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

# --- Constants --------------------------------------------------------------

# Models ≤2B by display name; Gemma-2-2B included per parks's call (actual
# 2.61B but marketed as "2B"). All trained successfully in Session 21.
MODELS: list[str] = [
    "unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
    "unsloth/Qwen2.5-0.5B-Instruct-bnb-4bit",
    "unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit",
    "unsloth/Qwen3-0.6B-unsloth-bnb-4bit",
    "unsloth/Qwen3-1.7B-unsloth-bnb-4bit",
    "unsloth/DeepSeek-R1-Distill-Qwen-1.5B-unsloth-bnb-4bit",
    "unsloth/SmolLM2-1.7B-Instruct-bnb-4bit",
    "unsloth/tinyllama-chat-bnb-4bit",
    "unsloth/gemma-2-2b-it-bnb-4bit",
]

TASKS: list[str] = ["classification", "tool_calling", "qa"]

# Closed sets extracted from seed_data/{task}/*_canonical.jsonl (Session 22)
CLS_LABELS: list[str] = ["ปัญหาการเงิน", "ปัญหาเทคนิค", "คำถามทั่วไป"]
TOOL_DEFS: list[dict] = [
    {"name": "light_on", "description": "Turn on a light",
     "parameters": {"room": {"type": "string", "required": False}}},
    {"name": "play_music", "description": "Play music",
     "parameters": {"genre": {"type": "string", "required": False}}},
    {"name": "set_oven", "description": "Set oven temperature",
     "parameters": {"celsius": {"type": "integer", "required": True}}},
    {"name": "set_volume", "description": "Set device volume 0-100",
     "parameters": {"level": {"type": "integer", "required": True}}},
    {"name": "start_timer", "description": "Start a countdown timer",
     "parameters": {"minutes": {"type": "integer", "required": True}}},
]

TASK_DESCRIPTIONS: dict[str, str] = {
    "classification": (
        "Classify Thai customer-support messages into one of 3 categories: "
        "billing/finance issues, technical issues, or general inquiries"
    ),
    "tool_calling": (
        "Translate spoken-style smart-home and kitchen commands into a JSON "
        "tool call with one of: light_on, play_music, set_oven, set_volume, start_timer"
    ),
    "qa": (
        "Answer Thai customer questions about our 30-day return/refund policy "
        "for an online retail store"
    ),
}

# Standard training config — small batch + short seq so even 2B fits 8GB.
# On RTX 5090 32GB this leaves tons of headroom.
TRAIN_CONFIG: dict = {
    "learning_rate": 2e-4,
    "num_train_epochs": 1,
    "per_device_train_batch_size": 4,
    "gradient_accumulation_steps": 4,
    "max_seq_length": 512,
    "warmup_ratio": 0.03,
    "weight_decay": 0.01,
    "lr_scheduler_type": "cosine",
    "seed": 42,
    "optim": "adamw_8bit",
    "packing": False,
    "lora": {"r": 16, "alpha": 32, "dropout": 0.05},
}

INFERENCE_PROMPTS: dict[str, str] = {
    "classification": "เครื่องไม่สามารถเชื่อมต่ออินเทอร์เน็ตได้ ช่วยแก้ไขด้วยครับ",
    "tool_calling": "Set the oven to 220 degrees Celsius",
    "qa": "ระยะเวลาคืนสินค้านานเท่าไหร่?",
}

# --- HTTP helpers (stdlib) --------------------------------------------------


def _req(
    method: str,
    url: str,
    *,
    body: Any = None,
    body_bytes: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> tuple[int, dict | list | str]:
    """Issue an HTTP request and return (status_code, parsed_body).

    Body is JSON-encoded unless body_bytes is set (for multipart upload).
    Returns the raw string body if response is not JSON.
    """
    hdrs = dict(headers or {})
    data: bytes | None = None
    if body_bytes is not None:
        data = body_bytes
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read()
        status = e.code
    except urllib.error.URLError as e:
        return 0, f"URLError: {e}"
    try:
        return status, json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return status, raw[:500].decode("utf-8", errors="replace")


def multipart_body(fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]) -> tuple[bytes, str]:
    """Build a multipart/form-data body. files: name -> (filename, bytes, ctype)."""
    boundary = f"----E2EBoundary{uuid.uuid4().hex}"
    crlf = b"\r\n"
    chunks: list[bytes] = []
    for k, v in fields.items():
        chunks.append(f"--{boundary}".encode())
        chunks.append(f'Content-Disposition: form-data; name="{k}"'.encode())
        chunks.append(b"")
        chunks.append(v.encode("utf-8"))
    for k, (fname, content, ctype) in files.items():
        chunks.append(f"--{boundary}".encode())
        chunks.append(
            f'Content-Disposition: form-data; name="{k}"; filename="{fname}"'.encode()
        )
        chunks.append(f"Content-Type: {ctype}".encode())
        chunks.append(b"")
        chunks.append(content)
    chunks.append(f"--{boundary}--".encode())
    chunks.append(b"")
    body = crlf.join(chunks)
    return body, f"multipart/form-data; boundary={boundary}"


# --- State persistence ------------------------------------------------------


class State:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {
            "projects": {},          # task -> project_id
            "seeds": {},             # task -> seed dataset_id
            "sdg": {},               # f"{task}__{mode}" -> {dataset_id, num_samples, status, error}
            "trainings": {},         # f"{model_short}__{task}" -> {training_id, artifact_id, status, mlflow_run_id, duration_s, error, exported, inference}
            "started_at": None,
            "finished_at": None,
        }
        if path.exists():
            self.data.update(json.loads(path.read_text(encoding="utf-8")))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")


# --- Logging ----------------------------------------------------------------


def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S", time.localtime())
    print(f"[{ts}] {msg}", flush=True)


# --- Step implementations ---------------------------------------------------


def ensure_projects(base_url: str, state: State) -> None:
    for task in TASKS:
        if task in state.data["projects"]:
            log(f"  ✓ project[{task}] cached: {state.data['projects'][task]}")
            continue
        name = f"E2E-Session22-{task}"
        status, body = _req(
            "POST", f"{base_url}/api/v1/projects",
            body={"name": name, "task_type": task, "description": f"Session 22 E2E for {task}"},
        )
        if status not in (200, 201):
            raise RuntimeError(f"create project[{task}] failed: {status} {body}")
        state.data["projects"][task] = body["id"]
        state.save()
        log(f"  ✓ project[{task}] created: {body['id']}")


def ensure_seeds(base_url: str, state: State) -> None:
    for task in TASKS:
        if task in state.data["seeds"]:
            log(f"  ✓ seed[{task}] cached: {state.data['seeds'][task]}")
            continue
        seed_path = Path(f"seed_data/{task}/{task}_canonical.jsonl")
        if not seed_path.exists():
            raise RuntimeError(f"seed file missing: {seed_path}")
        content = seed_path.read_bytes()
        body, ctype = multipart_body(
            fields={
                "project_id": state.data["projects"][task],
                "task_type": task,
                "name": f"seed-{task}-canonical",
            },
            files={"file": (seed_path.name, content, "application/x-ndjson")},
        )
        status, resp = _req(
            "POST", f"{base_url}/api/v1/datasets/upload-seed",
            body_bytes=body, headers={"Content-Type": ctype}, timeout=120,
        )
        if status not in (200, 201):
            raise RuntimeError(f"upload seed[{task}] failed: {status} {resp}")
        state.data["seeds"][task] = resp["dataset_id"]
        state.save()
        log(f"  ✓ seed[{task}] uploaded: {resp['dataset_id']} ({resp['num_samples']} rows)")


def submit_sdg_jobs(base_url: str, state: State, num_samples: int = 100) -> None:
    for task in TASKS:
        for mode in ["with_seed", "description_only"]:
            key = f"{task}__{mode}"
            slot = state.data["sdg"].setdefault(key, {})
            if slot.get("status") == "completed":
                log(f"  ✓ sdg[{key}] cached: {slot['dataset_id']} ({slot.get('num_samples', '?')} rows)")
                continue
            if slot.get("dataset_id"):
                log(f"  ↻ sdg[{key}] in flight: {slot['dataset_id']} — will poll")
                continue
            payload: dict[str, Any] = {
                "sdg_mode": mode,
                "project_id": state.data["projects"][task],
                "task_type": task,
                "task_description": TASK_DESCRIPTIONS[task],
                "num_samples": num_samples,
                "temperature": 0.9,
                "dataset_name": f"sdg-{task}-{mode}",
            }
            if mode == "with_seed":
                payload["seed_dataset_id"] = state.data["seeds"][task]
            else:
                if task == "classification":
                    payload["classification_config"] = {"labels": CLS_LABELS}
                elif task == "tool_calling":
                    payload["tool_calling_config"] = {"tool_definitions": TOOL_DEFS}
            status, resp = _req(
                "POST", f"{base_url}/api/v1/datasets/generate", body=payload, timeout=30,
            )
            if status != 202:
                slot["status"] = "submit_failed"
                slot["error"] = f"{status} {resp}"
                state.save()
                log(f"  ✗ sdg[{key}] submit failed: {status} {resp}")
                continue
            slot["dataset_id"] = resp["dataset_id"]
            slot["job_id"] = resp["job_id"]
            slot["status"] = "running"
            slot["submitted_at"] = time.time()
            state.save()
            log(f"  → sdg[{key}] submitted: dataset={resp['dataset_id']} job={resp['job_id']}")


def wait_for_sdg(base_url: str, state: State, max_wait_s: int = 900) -> None:
    deadline = time.time() + max_wait_s
    pending = [k for k, s in state.data["sdg"].items() if s.get("status") == "running"]
    while pending and time.time() < deadline:
        time.sleep(15)
        for key in list(pending):
            ds_id = state.data["sdg"][key]["dataset_id"]
            status, resp = _req("GET", f"{base_url}/api/v1/datasets/{ds_id}")
            if status != 200:
                log(f"  ! sdg[{key}] GET failed: {status} {resp}")
                continue
            num = resp.get("num_samples", 0)
            meta = resp.get("generation_metadata") or {}
            completed_at = meta.get("completed_at")
            failed = meta.get("failed_at") or meta.get("error")
            if failed:
                state.data["sdg"][key]["status"] = "failed"
                state.data["sdg"][key]["error"] = str(failed)
                state.save()
                log(f"  ✗ sdg[{key}] failed: {failed}")
                pending.remove(key)
            elif num > 0 and completed_at:
                state.data["sdg"][key]["status"] = "completed"
                state.data["sdg"][key]["num_samples"] = num
                state.data["sdg"][key]["completed_at"] = completed_at
                state.save()
                log(f"  ✓ sdg[{key}] complete: {num} rows")
                pending.remove(key)
            else:
                log(f"  · sdg[{key}] still running… (rows={num})")
    if pending:
        log(f"  ✗ sdg deadline hit; still pending: {pending}")


def model_short(model_id: str) -> str:
    """E.g. 'unsloth/Llama-3.2-1B-Instruct-bnb-4bit' -> 'llama-3.2-1b'."""
    name = model_id.split("/")[-1].lower()
    for s in ("-instruct-bnb-4bit", "-it-bnb-4bit", "-unsloth-bnb-4bit", "-bnb-4bit", "-chat"):
        name = name.replace(s, "")
    return name


def run_trainings(base_url: str, state: State, max_wait_each_s: int = 1200) -> None:
    for model in MODELS:
        short = model_short(model)
        for task in TASKS:
            key = f"{short}__{task}"
            slot = state.data["trainings"].setdefault(key, {})
            if slot.get("status") in ("completed", "failed", "submit_failed"):
                log(f"  ✓ train[{key}] cached: {slot.get('status')} (artifact={slot.get('artifact_id')})")
                continue
            # Pick the with_seed dataset for this task — proven reliable in S21.
            sdg_slot = state.data["sdg"].get(f"{task}__with_seed", {})
            dataset_id = sdg_slot.get("dataset_id")
            if not dataset_id or sdg_slot.get("status") != "completed":
                log(f"  ⊘ train[{key}] skipped (with_seed dataset not ready)")
                slot["status"] = "skipped_no_dataset"
                state.save()
                continue
            payload = {
                "project_id": state.data["projects"][task],
                "dataset_id": dataset_id,
                "base_model": model,
                "mode": "manual",
                "config": TRAIN_CONFIG,
            }
            status, resp = _req(
                "POST", f"{base_url}/api/v1/trainings", body=payload, timeout=30,
            )
            if status != 202:
                slot["status"] = "submit_failed"
                slot["error"] = f"{status} {resp}"
                state.save()
                log(f"  ✗ train[{key}] submit failed: {status} {resp}")
                continue
            slot["training_id"] = resp.get("training_id") or resp.get("id")
            slot["status"] = "running"
            slot["submitted_at"] = time.time()
            state.save()
            log(f"  → train[{key}] submitted: {slot['training_id']}")
            # Wait sequentially — worker concurrency=1 anyway
            t0 = time.time()
            deadline = t0 + max_wait_each_s
            terminal = None
            while time.time() < deadline:
                time.sleep(20)
                s2, r2 = _req("GET", f"{base_url}/api/v1/trainings/{slot['training_id']}")
                if s2 != 200:
                    log(f"  ! train[{key}] GET {s2}: {r2}")
                    continue
                jstatus = r2.get("status")
                if jstatus in ("completed", "failed", "cancelled"):
                    terminal = jstatus
                    slot["status"] = jstatus
                    slot["mlflow_run_id"] = r2.get("mlflow_run_id")
                    slot["duration_s"] = int(time.time() - t0)
                    if jstatus == "failed":
                        slot["error"] = r2.get("error_message") or r2.get("error") or "?"
                    # find artifact
                    s3, r3 = _req(
                        "GET",
                        f"{base_url}/api/v1/models?training_job_id={slot['training_id']}&limit=10",
                    )
                    if s3 == 200 and isinstance(r3, dict) and r3.get("items"):
                        slot["artifact_id"] = r3["items"][0]["id"]
                    state.save()
                    log(f"  {'✓' if jstatus == 'completed' else '✗'} train[{key}] {jstatus} ({slot['duration_s']}s)"
                        + (f" artifact={slot.get('artifact_id')}" if slot.get("artifact_id") else "")
                        + (f" err={slot.get('error', '')[:120]}" if jstatus == "failed" else ""))
                    break
                else:
                    elapsed = int(time.time() - t0)
                    log(f"  · train[{key}] {jstatus} ({elapsed}s)")
            if terminal is None:
                slot["status"] = "timeout"
                slot["duration_s"] = max_wait_each_s
                state.save()
                log(f"  ✗ train[{key}] timeout after {max_wait_each_s}s")


def run_exports(base_url: str, state: State, max_wait_s: int = 600) -> None:
    for key, slot in state.data["trainings"].items():
        if slot.get("status") != "completed" or not slot.get("artifact_id"):
            continue
        exp = slot.setdefault("exported", {})
        if exp.get("status") == "ok":
            log(f"  ✓ export[{key}] cached: {exp.get('ollama_tag')}")
            continue
        aid = slot["artifact_id"]
        status, resp = _req(
            "POST", f"{base_url}/api/v1/models/{aid}/export",
            body={"format": "gguf", "quantization": "q4_k_m"}, timeout=30,
        )
        if status != 202:
            exp["status"] = "submit_failed"
            exp["error"] = f"{status} {resp}"
            state.save()
            log(f"  ✗ export[{key}] submit {status}: {resp}")
            continue
        exp["status"] = "running"
        exp["submitted_at"] = time.time()
        state.save()
        log(f"  → export[{key}] submitted (artifact={aid})")
        t0 = time.time()
        while time.time() - t0 < max_wait_s:
            time.sleep(15)
            s2, r2 = _req("GET", f"{base_url}/api/v1/models/{aid}")
            if s2 != 200:
                continue
            if r2.get("gguf_uri"):
                exp["status"] = "ok"
                exp["gguf_uri"] = r2["gguf_uri"]
                exp["ollama_tag"] = r2.get("ollama_model_tag")
                exp["duration_s"] = int(time.time() - t0)
                state.save()
                log(f"  ✓ export[{key}] ok ({exp['duration_s']}s) tag={exp.get('ollama_tag')}")
                break
            if r2.get("export_error_message"):
                exp["status"] = "failed"
                exp["error"] = r2["export_error_message"]
                state.save()
                log(f"  ✗ export[{key}] failed: {r2['export_error_message'][:120]}")
                break
        else:
            exp["status"] = "timeout"
            state.save()
            log(f"  ✗ export[{key}] timeout after {max_wait_s}s")


def run_inferences(base_url: str, state: State) -> None:
    for key, slot in state.data["trainings"].items():
        exp = slot.get("exported") or {}
        if exp.get("status") != "ok" or not exp.get("ollama_tag"):
            continue
        inf = slot.setdefault("inference", {})
        if inf.get("status") == "ok":
            log(f"  ✓ inf[{key}] cached")
            continue
        task = key.split("__")[1]
        prompt = INFERENCE_PROMPTS[task]
        payload = {
            "model": exp["ollama_tag"],
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 80,
            "temperature": 0.1,
        }
        t0 = time.time()
        status, resp = _req(
            "POST", f"{base_url}/api/v1/inference/chat/completions",
            body=payload, timeout=120,
        )
        latency_ms = int((time.time() - t0) * 1000)
        if status != 200:
            inf["status"] = "failed"
            inf["error"] = f"{status} {resp}"
            inf["latency_ms"] = latency_ms
            state.save()
            log(f"  ✗ inf[{key}] {status}: {str(resp)[:200]}")
            continue
        try:
            content = resp["choices"][0]["message"]["content"]
            finish = resp["choices"][0].get("finish_reason")
        except (KeyError, IndexError, TypeError):
            inf["status"] = "malformed"
            inf["error"] = f"unexpected body: {str(resp)[:200]}"
            state.save()
            log(f"  ✗ inf[{key}] malformed body")
            continue
        inf["status"] = "ok"
        inf["latency_ms"] = latency_ms
        inf["finish_reason"] = finish
        inf["response_preview"] = content[:200]
        state.save()
        log(f"  ✓ inf[{key}] {latency_ms}ms finish={finish} resp={content[:80]!r}")


def print_summary(state: State) -> None:
    print()
    print("=" * 80)
    print("SESSION 22 SUMMARY")
    print("=" * 80)
    sdg_ok = sum(1 for s in state.data["sdg"].values() if s.get("status") == "completed")
    print(f"SDG datasets: {sdg_ok}/{len(state.data['sdg'])} completed")
    for k, s in state.data["sdg"].items():
        print(f"  {s.get('status', '?'):>10}  {k}  rows={s.get('num_samples', '-')}  id={s.get('dataset_id', '-')}")
    print()
    train_total = len(state.data["trainings"])
    train_ok = sum(1 for s in state.data["trainings"].values() if s.get("status") == "completed")
    export_ok = sum(1 for s in state.data["trainings"].values() if (s.get("exported") or {}).get("status") == "ok")
    inf_ok = sum(1 for s in state.data["trainings"].values() if (s.get("inference") or {}).get("status") == "ok")
    print(f"Trainings : {train_ok}/{train_total} completed")
    print(f"Exports   : {export_ok}/{train_total} ok")
    print(f"Inference : {inf_ok}/{train_total} ok")
    print()
    print(f"{'KEY':<45} {'TRAIN':<10} {'EXPORT':<8} {'INF':<6} {'DUR':<6} {'TAG'}")
    print("-" * 110)
    for k in sorted(state.data["trainings"]):
        s = state.data["trainings"][k]
        e = s.get("exported") or {}
        i = s.get("inference") or {}
        print(f"{k:<45} {s.get('status', '-'):<10} {e.get('status', '-'):<8} {i.get('status', '-'):<6} "
              f"{s.get('duration_s', '-'):<6} {e.get('ollama_tag', '-')}")


# --- Main -------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--state-dir", default="/tmp/e2e")
    ap.add_argument("--num-samples", type=int, default=100)
    ap.add_argument("--skip-sdg", action="store_true")
    ap.add_argument("--skip-train", action="store_true")
    ap.add_argument("--skip-export", action="store_true")
    ap.add_argument("--skip-inference", action="store_true")
    ap.add_argument("--summary-only", action="store_true")
    args = ap.parse_args()

    state_path = Path(args.state_dir) / "state.json"
    state = State(state_path)
    if state.data["started_at"] is None:
        state.data["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        state.save()

    if args.summary_only:
        print_summary(state)
        return 0

    log("STEP 1: ensure projects (3)")
    ensure_projects(args.base_url, state)
    log("STEP 2: ensure seed uploads (3)")
    ensure_seeds(args.base_url, state)

    if not args.skip_sdg:
        log(f"STEP 3: submit SDG (6 jobs × {args.num_samples} rows)")
        submit_sdg_jobs(args.base_url, state, num_samples=args.num_samples)
        log("STEP 4: wait for SDG completion")
        wait_for_sdg(args.base_url, state)

    if not args.skip_train:
        log(f"STEP 5: run trainings ({len(MODELS)} models × {len(TASKS)} tasks = {len(MODELS) * len(TASKS)})")
        run_trainings(args.base_url, state)

    if not args.skip_export:
        log("STEP 6: export to GGUF + Ollama register")
        run_exports(args.base_url, state)

    if not args.skip_inference:
        log("STEP 7: inference smoke tests")
        run_inferences(args.base_url, state)

    state.data["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    state.save()
    print_summary(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
