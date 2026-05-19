"""End-to-end smoke: 5 base models × tool_calling × 1000 SDG samples.

Pipeline per base model:
  POST /trainings (manual) → poll until status=completed
  POST /evaluations (rule-based + LLM judge) → poll until status=completed
  POST /models/{id}/export gguf q4_k_m → poll until gguf_uri set or error
  POST /inference/chat/completions on slm/{id8}

Master flow:
  1. POST /projects (tool_calling)
  2. POST /datasets/generate (description_only, 1000 samples, 8 tool defs)
  3. Poll dataset.status until ready
  4. For each base: run the per-model pipeline (best-effort — continue on failure)
  5. Write JSON summary to OUT/state.json
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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(*a: object) -> None:
    msg = "[" + datetime.now(timezone.utc).strftime("%H:%M:%S") + "] " + " ".join(str(x) for x in a)
    print(msg, flush=True)
    with LOG_FILE.open("a") as f:
        f.write(msg + "\n")


def save_state(d: dict) -> None:
    STATE_FILE.write_text(json.dumps(d, indent=2, default=str))


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
    log(f"TIMEOUT [{label}] after {timeout_s}s; last={last}")
    return last


TOOL_DEFS = [
    {
        "name": "get_weather",
        "description": "Look up the current weather for a city.",
        "parameters": {
            "location": {"type": "string", "required": True, "description": "City and country, e.g. 'Bangkok, Thailand'"},
            "units": {"type": "string", "required": False, "description": "'metric' or 'imperial' (default metric)"},
        },
    },
    {
        "name": "send_email",
        "description": "Send an email to one recipient.",
        "parameters": {
            "to": {"type": "string", "required": True},
            "subject": {"type": "string", "required": True},
            "body": {"type": "string", "required": True},
        },
    },
    {
        "name": "search_web",
        "description": "Run a web search and return top results.",
        "parameters": {
            "query": {"type": "string", "required": True},
            "num_results": {"type": "integer", "required": False},
        },
    },
    {
        "name": "set_timer",
        "description": "Set a countdown timer in seconds.",
        "parameters": {
            "duration_seconds": {"type": "integer", "required": True},
            "label": {"type": "string", "required": False},
        },
    },
    {
        "name": "create_calendar_event",
        "description": "Add an event to the calendar.",
        "parameters": {
            "title": {"type": "string", "required": True},
            "start_time": {"type": "string", "required": True, "description": "ISO-8601, e.g. 2026-01-30T15:00:00Z"},
            "duration_minutes": {"type": "integer", "required": True},
        },
    },
    {
        "name": "get_stock_price",
        "description": "Look up the latest stock price.",
        "parameters": {
            "ticker": {"type": "string", "required": True, "description": "Stock symbol, e.g. 'AAPL'"},
        },
    },
    {
        "name": "translate_text",
        "description": "Translate text into a target language.",
        "parameters": {
            "text": {"type": "string", "required": True},
            "target_language": {"type": "string", "required": True, "description": "ISO-639-1, e.g. 'th', 'en', 'ja'"},
        },
    },
    {
        "name": "calculate",
        "description": "Evaluate an arithmetic expression.",
        "parameters": {
            "expression": {"type": "string", "required": True, "description": "e.g. '(12 + 3) * 4'"},
        },
    },
]


BASES = [
    ("Llama-3.2-1B", "unsloth/Llama-3.2-1B-Instruct-bnb-4bit"),
    ("Qwen2.5-0.5B", "unsloth/Qwen2.5-0.5B-Instruct-bnb-4bit"),
    ("Qwen3-0.6B", "unsloth/Qwen3-0.6B-unsloth-bnb-4bit"),
    ("SmolLM2-1.7B", "unsloth/SmolLM2-1.7B-Instruct-bnb-4bit"),
    ("Gemma-2-2B", "unsloth/gemma-2-2b-it-bnb-4bit"),
]


def main() -> None:
    state: dict = {"started": now_iso(), "models": []}

    # ----- 1. Create project ---------------------------------------------------
    code, p = http("POST", "/projects", {
        "name": f"e2e-tool-calling-5-models-{datetime.now().strftime('%Y%m%d-%H%M')}",
        "task_type": "tool_calling",
        "description": "Session 21 — verify 5 SLM bases can be fine-tuned for tool calling end-to-end.",
    })
    if code != 201:
        log("project create FAILED", code, p)
        state["project_error"] = p
        save_state(state)
        return
    project_id = p["id"]
    log("project_id =", project_id)
    state["project_id"] = project_id
    save_state(state)

    # ----- 2. SDG 1000 samples -------------------------------------------------
    sdg_body = {
        "sdg_mode": "description_only",
        "project_id": project_id,
        "task_type": "tool_calling",
        "task_description": (
            "Translate a user's natural-language instruction into a single JSON tool call. "
            "Inputs cover everyday home/office assistant tasks: weather lookups, email composition, "
            "web search, timers, calendar events, stock prices, translation, and arithmetic. "
            "Outputs must always be valid JSON matching one of the provided tool definitions exactly."
        ),
        "num_samples": 1000,
        "temperature": 0.9,
        "dataset_name": "tool-calling-mixed-1000",
        "tool_calling_config": {"tool_definitions": TOOL_DEFS},
    }
    code, r = http("POST", "/datasets/generate", sdg_body, timeout=60)
    if code != 202:
        log("sdg submit FAILED", code, r)
        state["sdg_error"] = r
        save_state(state)
        return
    dataset_id = r["dataset_id"]
    sdg_job_id = r["job_id"]
    log("sdg submitted dataset_id =", dataset_id, "job_id =", sdg_job_id)
    state["dataset_id"] = dataset_id
    state["sdg_job_id"] = sdg_job_id
    save_state(state)

    def get_dataset():
        c, d = http("GET", f"/datasets/{dataset_id}")
        return d if c == 200 else None

    sdg = wait_until(
        get_dataset,
        lambda d: bool(d) and d.get("status") in ("ready", "completed", "failed"),
        "SDG complete",
        timeout_s=3600,
        interval_s=15,
    )
    if not sdg or sdg.get("status") not in ("ready", "completed"):
        log("sdg did not complete:", sdg)
        state["sdg_final"] = sdg
        save_state(state)
        return
    state["sdg_status"] = sdg.get("status")
    state["sdg_num_samples"] = sdg.get("num_samples")
    log("sdg DONE — num_samples =", sdg.get("num_samples"))
    save_state(state)

    # ----- 3. For each base model: train → eval → export → inference ----------
    for nick, base_id in BASES:
        entry: dict = {"nick": nick, "base": base_id, "started": now_iso()}
        log("=" * 60)
        log("BASE:", nick, "(", base_id, ")")
        log("=" * 60)

        # ---- TRAIN ----
        train_body = {
            "mode": "manual",
            "project_id": project_id,
            "dataset_id": dataset_id,
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
            state["models"].append(entry)
            save_state(state)
            continue
        training_id = t["training_id"]
        entry["training_id"] = training_id
        log("  train submitted training_id=", training_id)
        save_state({**state, "models": state["models"] + [entry]})

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
        log("  TRAIN", entry["train_status"], "err=", entry["train_error_message"])
        if entry["train_status"] != "completed":
            state["models"].append(entry)
            save_state(state)
            continue

        # find artifact
        c, arts = http("GET", f"/models?training_job_id={training_id}")
        if c != 200 or not arts.get("items"):
            log("  no artifact rows returned")
            entry["artifact_error"] = arts
            state["models"].append(entry)
            save_state(state)
            continue
        artifact = arts["items"][0]
        artifact_id = artifact["id"]
        entry["artifact_id"] = artifact_id
        log("  artifact_id=", artifact_id)
        save_state({**state, "models": state["models"] + [entry]})

        # ---- EVAL ----
        eval_body = {
            "model_artifact_id": artifact_id,
            "dataset_id": dataset_id,
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
            log("  EVAL", entry["eval_status"], "metrics=", entry["eval_metrics"], "judge=", entry["llm_judge_score"])

        # ---- EXPORT GGUF ----
        c, x = http("POST", f"/models/{artifact_id}/export", {"export_format": "gguf", "quantization": "q4_k_m"}, timeout=60)
        if c != 202:
            log("  EXPORT submit FAILED", c, x)
            entry["export_error_submit"] = x
        else:
            entry["export_job_id"] = x.get("job_id")
            log("  export submitted job_id=", entry["export_job_id"])

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
            log("  EXPORT gguf_uri=", entry["gguf_uri"], "tag=", entry["ollama_model_tag"], "err=", entry["export_error_message"])

        # ---- INFERENCE TEST ----
        if entry.get("ollama_model_tag"):
            chat_body = {
                "model": entry["ollama_model_tag"],
                "messages": [
                    {"role": "user", "content": "What's the weather like in Bangkok right now?"},
                ],
                "temperature": 0.2,
                "max_tokens": 80,
            }
            c, resp = http("POST", "/inference/chat/completions", chat_body, timeout=120)
            entry["inference_status"] = c
            try:
                entry["inference_response"] = resp["choices"][0]["message"]["content"] if c == 200 else resp
            except Exception:
                entry["inference_response"] = resp
            log("  INFERENCE code=", c, "resp=", str(entry.get("inference_response"))[:200])

        entry["ended"] = now_iso()
        state["models"].append(entry)
        save_state(state)

    state["ended"] = now_iso()
    save_state(state)
    log("=== ALL DONE ===")


if __name__ == "__main__":
    main()
