"""Second-pass orchestrator after attempt 1 found 3 bugs:

  1. Trainer chat-template key 'gemma-2' → 'gemma2' (Unsloth's CHAT_TEMPLATES)
  2. Export field name is `format`, not `export_format`
  3. Order is TRAIN → EXPORT → EVAL → INFERENCE (eval depends on Ollama tag)

For the 4 base models that already trained successfully in attempt 1, this
script reuses their `artifact_id` and skips re-training. Gemma-2-2B gets a
full re-run after the trainer fix lands.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "http://localhost:8000/api/v1"
OUT = Path("/tmp/e2e-tool-calling")
STATE_FILE = OUT / "state.json"
LOG_FILE = OUT / "run.log"

PROJECT_ID = "8cc7cb6b-b912-44e6-8338-7622f7c85018"
DATASET_ID = "05f5bdb4-278e-42dd-b249-9a4cff6583b0"

# nick -> {base, training_id, artifact_id} (from attempt 1 state)
PRE_TRAINED = {
    "Llama-3.2-1B":  {"base": "unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
                       "training_id": "e117787d-1f65-4bea-bf58-667a82f8d949",
                       "artifact_id": "ce321339-242a-4fa6-a44d-f7baed560460"},
    "Qwen2.5-0.5B":  {"base": "unsloth/Qwen2.5-0.5B-Instruct-bnb-4bit",
                       "training_id": "347b6231-36cf-4519-b731-83d7e9a44d2c",
                       "artifact_id": "c09595e1-9679-4e38-ac21-fd83c24c84b1"},
    "Qwen3-0.6B":    {"base": "unsloth/Qwen3-0.6B-unsloth-bnb-4bit",
                       "training_id": "9b9c4399-cff8-4cd0-ae62-2a1f192e379a",
                       "artifact_id": "e8426655-ccdb-437d-98a4-efde32486ddc"},
    "SmolLM2-1.7B":  {"base": "unsloth/SmolLM2-1.7B-Instruct-bnb-4bit",
                       "training_id": "420af91e-5d37-4f28-9dbe-b43e8e4d94d5",
                       "artifact_id": "523eb4b3-54f2-4010-9fe1-5aed14616fc0"},
}

# Needs a fresh training run after the trainer fix.
TO_TRAIN = [
    ("Gemma-2-2B", "unsloth/gemma-2-2b-it-bnb-4bit"),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(*a: object) -> None:
    msg = "[" + datetime.now(timezone.utc).strftime("%H:%M:%S") + "] " + " ".join(str(x) for x in a)
    print(msg, flush=True)
    with LOG_FILE.open("a") as f:
        f.write(msg + "\n")


def http(method: str, path: str, body: dict | None = None, timeout: int = 60):
    url = path if path.startswith("http") else f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode()
            return r.status, (json.loads(text) if text else None)
    except urllib.error.HTTPError as e:
        text = e.read().decode()
        try:
            body_obj = json.loads(text)
        except Exception:
            body_obj = text
        return e.code, body_obj
    except urllib.error.URLError as e:
        return 0, {"error": str(e)}


def wait_until(getter, predicate, label: str, *, timeout_s: int, interval_s: int = 10):
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        last = getter()
        if predicate(last):
            return last
        time.sleep(interval_s)
    log(f"TIMEOUT [{label}] after {timeout_s}s")
    return last


def run_export_eval_infer(entry: dict, nick: str, artifact_id: str, save) -> None:
    """Run EXPORT → EVAL → INFERENCE for a given artifact_id."""

    # ---- EXPORT GGUF ----
    c, x = http(
        "POST", f"/models/{artifact_id}/export",
        {"format": "gguf", "quantization": "q4_k_m"},
        timeout=60,
    )
    if c != 202:
        log(f"  EXPORT submit FAILED for {nick}", c, x)
        entry["export_error_submit"] = x
        save()
        return
    entry["export_job_id"] = x.get("job_id")
    log("  export submitted job_id=", entry["export_job_id"])
    save()

    def get_artifact():
        c2, d = http("GET", f"/models/{artifact_id}")
        return d if c2 == 200 else None

    art = wait_until(
        get_artifact,
        lambda d: bool(d) and (d.get("gguf_uri") or d.get("export_error_message")),
        f"export {nick}",
        timeout_s=1800,
        interval_s=15,
    )
    entry["gguf_uri"] = (art or {}).get("gguf_uri")
    entry["ollama_model_tag"] = (art or {}).get("ollama_model_tag")
    entry["export_error_message"] = (art or {}).get("export_error_message")
    log("  EXPORT gguf=", "yes" if entry.get("gguf_uri") else "no",
        "tag=", entry["ollama_model_tag"], "err=", entry.get("export_error_message"))
    save()

    if not entry.get("ollama_model_tag"):
        return

    # ---- EVAL (now that Ollama tag exists) ----
    eval_body = {
        "model_artifact_id": artifact_id,
        "dataset_id": DATASET_ID,
        "use_llm_judge": True,
    }
    c, e = http("POST", "/evaluations", eval_body, timeout=60)
    if c != 202:
        log(f"  EVAL submit FAILED for {nick}", c, e)
        entry["eval_error_submit"] = e
        save()
    else:
        eval_id = e["evaluation_id"]
        entry["evaluation_id"] = eval_id
        log("  eval submitted evaluation_id=", eval_id)
        save()

        def get_eval():
            c2, d = http("GET", f"/evaluations/{eval_id}")
            return d if c2 == 200 else None

        ev = wait_until(
            get_eval,
            lambda d: bool(d) and d.get("status") in ("completed", "failed", "cancelled"),
            f"eval {nick}",
            timeout_s=2400,
            interval_s=15,
        )
        entry["eval_status"] = (ev or {}).get("status")
        entry["eval_metrics"] = (ev or {}).get("metrics_json")
        entry["llm_judge_score"] = (ev or {}).get("llm_judge_score")
        entry["eval_error_message"] = (ev or {}).get("error_message")
        log("  EVAL", entry.get("eval_status"), "judge=", entry.get("llm_judge_score"))
        save()

    # ---- INFERENCE TEST ----
    chat_body = {
        "model": entry["ollama_model_tag"],
        "messages": [
            {"role": "user", "content": "What's the weather like in Bangkok right now?"},
        ],
        "temperature": 0.2,
        "max_tokens": 100,
    }
    c, resp = http("POST", "/inference/chat/completions", chat_body, timeout=180)
    entry["inference_status"] = c
    try:
        entry["inference_response"] = resp["choices"][0]["message"]["content"] if c == 200 else resp
    except Exception:
        entry["inference_response"] = resp
    log("  INFERENCE code=", c, "resp[0..200]=", str(entry.get("inference_response"))[:200])
    save()


def main() -> None:
    state: dict = {}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
        except Exception:
            state = {}

    state["fix_attempt_started"] = now_iso()
    state.setdefault("attempt2_models", [])

    def save():
        STATE_FILE.write_text(json.dumps(state, indent=2, default=str))

    save()
    log("=" * 60)
    log("ATTEMPT 2 — fix order + field name + gemma-2 chat template")
    log("=" * 60)

    # --- A) Reuse the 4 already-trained artifacts ---
    for nick, info in PRE_TRAINED.items():
        entry: dict = {
            "nick": nick,
            "base": info["base"],
            "training_id": info["training_id"],
            "artifact_id": info["artifact_id"],
            "reused_training": True,
            "started": now_iso(),
        }
        state["attempt2_models"].append(entry)
        save()
        log("=" * 60)
        log("REUSE-TRAIN:", nick, "artifact_id=", info["artifact_id"])
        log("=" * 60)
        run_export_eval_infer(entry, nick, info["artifact_id"], save)
        entry["ended"] = now_iso()
        save()

    # --- B) Re-train Gemma-2-2B (was failing on chat template before fix) ---
    for nick, base_id in TO_TRAIN:
        entry: dict = {"nick": nick, "base": base_id, "reused_training": False,
                        "started": now_iso()}
        state["attempt2_models"].append(entry)
        save()
        log("=" * 60)
        log("RE-TRAIN:", nick, "(", base_id, ")")
        log("=" * 60)

        train_body = {
            "mode": "manual",
            "project_id": PROJECT_ID,
            "dataset_id": DATASET_ID,
            "base_model": base_id,
            "training_name": f"e2e-attempt2-{nick}",
            "manual_config": {
                "learning_rate": 2e-4,
                "num_train_epochs": 1,
                "per_device_train_batch_size": 2,
                "gradient_accumulation_steps": 8,
                "max_seq_length": 512,
                "warmup_ratio": 0.03,
                "weight_decay": 0.01,
                "lr_scheduler_type": "cosine",
                "optim": "adamw_8bit",
                "lora": {"r": 16, "alpha": 32, "dropout": 0.05},
            },
        }
        c, t = http("POST", "/trainings", train_body, timeout=60)
        if c != 202:
            log("  TRAIN submit FAILED", c, t)
            entry["train_error_submit"] = t
            save()
            continue
        training_id = t["training_id"]
        entry["training_id"] = training_id
        log("  train submitted training_id=", training_id)
        save()

        def get_training():
            c2, d = http("GET", f"/trainings/{training_id}")
            return d if c2 == 200 else None

        tj = wait_until(
            get_training,
            lambda d: bool(d) and d.get("status") in ("completed", "failed", "cancelled"),
            f"train {nick}",
            timeout_s=3600,
            interval_s=20,
        )
        entry["train_status"] = (tj or {}).get("status")
        entry["train_error_message"] = (tj or {}).get("error_message")
        log("  TRAIN", entry["train_status"], "err=", entry["train_error_message"])
        save()
        if entry["train_status"] != "completed":
            continue

        c, arts = http("GET", f"/models?training_job_id={training_id}")
        if c != 200 or not arts.get("items"):
            entry["artifact_error"] = arts
            save()
            continue
        artifact_id = arts["items"][0]["id"]
        entry["artifact_id"] = artifact_id
        save()

        run_export_eval_infer(entry, nick, artifact_id, save)
        entry["ended"] = now_iso()
        save()

    state["ended"] = now_iso()
    save()
    log("=== ATTEMPT 2 ALL DONE ===")


if __name__ == "__main__":
    main()
