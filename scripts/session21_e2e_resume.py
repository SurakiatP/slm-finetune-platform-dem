"""Resume the 5-model e2e smoke from after SDG (which already completed).

Reuses project_id + dataset_id from the first orchestrator run. Skips SDG.
For each base: train → eval → export → inference.
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
OUT.mkdir(parents=True, exist_ok=True)
STATE_FILE = OUT / "state.json"
LOG_FILE = OUT / "run.log"

PROJECT_ID = "8cc7cb6b-b912-44e6-8338-7622f7c85018"
DATASET_ID = "05f5bdb4-278e-42dd-b249-9a4cff6583b0"


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


BASES = [
    ("Llama-3.2-1B", "unsloth/Llama-3.2-1B-Instruct-bnb-4bit"),
    ("Qwen2.5-0.5B", "unsloth/Qwen2.5-0.5B-Instruct-bnb-4bit"),
    ("Qwen3-0.6B", "unsloth/Qwen3-0.6B-unsloth-bnb-4bit"),
    ("SmolLM2-1.7B", "unsloth/SmolLM2-1.7B-Instruct-bnb-4bit"),
    ("Gemma-2-2B", "unsloth/gemma-2-2b-it-bnb-4bit"),
]


def main() -> None:
    # Carry over what we know already.
    state: dict = {}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
        except Exception:
            state = {}

    state["resumed_at"] = now_iso()
    state["project_id"] = PROJECT_ID
    state["dataset_id"] = DATASET_ID
    state.setdefault("models", [])
    state["sdg_status"] = "completed"
    state["sdg_num_samples"] = 1000

    def save():
        STATE_FILE.write_text(json.dumps(state, indent=2, default=str))

    save()
    log("=== RESUME ===")
    log("project_id =", PROJECT_ID)
    log("dataset_id =", DATASET_ID)

    for nick, base_id in BASES:
        entry: dict = {"nick": nick, "base": base_id, "started": now_iso()}
        state["models"].append(entry)
        save()

        log("=" * 60)
        log("BASE:", nick, "(", base_id, ")")
        log("=" * 60)

        # ---- TRAIN ----
        train_body = {
            "mode": "manual",
            "project_id": PROJECT_ID,
            "dataset_id": DATASET_ID,
            "base_model": base_id,
            "training_name": f"e2e-{nick}",
            "manual_config": {
                "learning_rate": 2e-4,
                "num_train_epochs": 1,
                "per_device_train_batch_size": 4,
                "gradient_accumulation_steps": 4,
                "max_seq_length": 512,
                "warmup_ratio": 0.03,
                "weight_decay": 0.01,
                "lr_scheduler_type": "cosine",
                "optim": "adamw_8bit",
                "lora": {
                    "r": 16,
                    "alpha": 32,
                    "dropout": 0.05,
                },
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
        entry["train_job_id"] = t.get("job_id")
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
        entry["mlflow_run_id"] = (tj or {}).get("mlflow_run_id")
        entry["train_ended_at"] = (tj or {}).get("ended_at")
        log("  TRAIN", entry["train_status"], "err=", entry["train_error_message"])
        save()
        if entry["train_status"] != "completed":
            continue

        # find artifact
        c, arts = http("GET", f"/models?training_job_id={training_id}")
        if c != 200 or not arts.get("items"):
            log("  no artifact rows returned")
            entry["artifact_error"] = arts
            save()
            continue
        artifact = arts["items"][0]
        artifact_id = artifact["id"]
        entry["artifact_id"] = artifact_id
        entry["lora_uri"] = artifact.get("lora_uri")
        log("  artifact_id=", artifact_id)
        save()

        # ---- EVAL ----
        eval_body = {
            "model_artifact_id": artifact_id,
            "dataset_id": DATASET_ID,
            "use_llm_judge": True,
        }
        c, e = http("POST", "/evaluations", eval_body, timeout=60)
        if c != 202:
            log("  EVAL submit FAILED", c, e)
            entry["eval_error_submit"] = e
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
            log("  EVAL", entry["eval_status"], "judge=", entry["llm_judge_score"])
            save()

        # ---- EXPORT GGUF ----
        c, x = http("POST", f"/models/{artifact_id}/export",
                    {"export_format": "gguf", "quantization": "q4_k_m"}, timeout=60)
        if c != 202:
            log("  EXPORT submit FAILED", c, x)
            entry["export_error_submit"] = x
        else:
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
                "tag=", entry["ollama_model_tag"], "err=", entry["export_error_message"])
            save()

        # ---- INFERENCE TEST ----
        if entry.get("ollama_model_tag"):
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

        entry["ended"] = now_iso()
        save()

    state["ended"] = now_iso()
    save()
    log("=== ALL DONE ===")


if __name__ == "__main__":
    main()
