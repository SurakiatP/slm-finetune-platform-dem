"""Session 25 - End-to-end smoke for SDG holdout flow (HO.8 / Phase 11).

Verifies the new leak-free evaluation pipeline added in Session 20:

  POST /datasets/generate  with  holdout_size > 0
    -> parent dataset (train rows) + child dataset (holdout rows, parent_dataset_id set)
  POST /trainings          on parent
  POST /models/{id}/export gguf
  POST /evaluations        on **child** (holdout) -> leak-free judge score

Runs once per task type (classification / tool_calling / qa) or one chosen task
type via --task. Each task type takes ~30-45 min on an RTX 3060; all three is
~2 hours. State is persisted to /tmp/e2e-holdout/state.json so a crashed run
can be resumed by re-running the script - completed steps for a task_type are
skipped on the next invocation.

Run from inside the api/worker host (vast.ai box), pointing at the local API:

    python scripts/session25_holdout_e2e.py                  # all 3 task types
    python scripts/session25_holdout_e2e.py --task qa        # just QA
    python scripts/session25_holdout_e2e.py --num 60 --holdout 20

Pre-flight (must be done before running this script):
  * docker compose up -d  (api, worker, postgres, redis, minio, mlflow, ollama)
  * docker compose exec -T api alembic upgrade head   <- includes 0003_dataset_parent_id
  * .env has OPENROUTER_API_KEY set
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "http://localhost:8000/api/v1"
OUT = Path("/tmp/e2e-holdout")
OUT.mkdir(parents=True, exist_ok=True)
STATE_FILE = OUT / "state.json"
LOG_FILE = OUT / "run.log"

BASE_MODEL = "unsloth/Llama-3.2-1B-Instruct-bnb-4bit"


# ---------- helpers --------------------------------------------------------


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(*a: object) -> None:
    msg = "[" + datetime.now(timezone.utc).strftime("%H:%M:%S") + "] " + " ".join(str(x) for x in a)
    print(msg, flush=True)
    with LOG_FILE.open("a") as f:
        f.write(msg + "\n")


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"started": now_iso(), "tasks": {}}


def save_state(s: dict) -> None:
    STATE_FILE.write_text(json.dumps(s, indent=2, default=str))


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
            obj = json.loads(text)
        except Exception:
            obj = text
        return e.code, obj
    except urllib.error.URLError as e:
        return 0, {"error": str(e)}


def wait_until(getter, predicate, label: str, *, timeout_s: int, interval_s: int = 15):
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        last = getter()
        if predicate(last):
            return last
        time.sleep(interval_s)
    log(f"TIMEOUT [{label}] after {timeout_s}s; last={last}")
    return last


# ---------- per-task-type configs -----------------------------------------


CLS_LABELS = ["billing", "technical", "shipping", "account", "general"]

TOOL_DEFS = [
    {
        "name": "get_weather",
        "description": "Look up the current weather for a city.",
        "parameters": {
            "location": {"type": "string", "required": True},
            "units": {"type": "string", "required": False},
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
        "name": "calculate",
        "description": "Evaluate an arithmetic expression.",
        "parameters": {
            "expression": {"type": "string", "required": True},
        },
    },
]


def sdg_body_for(task: str, project_id: str, num: int, holdout: int) -> dict:
    base = {
        "sdg_mode": "description_only",
        "project_id": project_id,
        "task_type": task,
        "num_samples": num,
        "holdout_size": holdout,
        "temperature": 0.9,
    }
    if task == "classification":
        base["task_description"] = (
            "Classify customer support tickets into one of: billing, technical, "
            "shipping, account, general. Each input is a short English customer message."
        )
        base["classification_config"] = {"labels": CLS_LABELS}
    elif task == "tool_calling":
        base["task_description"] = (
            "Translate a natural-language instruction into a single JSON tool call. "
            "Outputs must be valid JSON matching one of the provided tool definitions exactly."
        )
        base["tool_calling_config"] = {"tool_definitions": TOOL_DEFS}
    elif task == "qa":
        base["task_description"] = (
            "Answer general-knowledge questions concisely (1-3 sentences). "
            "Questions cover everyday topics: geography, history, basic science, "
            "common technology."
        )
    else:
        raise ValueError(f"unknown task: {task}")
    return base


# ---------- main per-task flow --------------------------------------------


def run_task_type(task: str, num: int, holdout: int, state: dict) -> None:
    """Idempotent: re-running picks up where it left off via state.json."""
    entry: dict = state["tasks"].get(task, {})
    state["tasks"][task] = entry
    entry.setdefault("started", now_iso())
    log("=" * 60)
    log(f"TASK_TYPE={task}  num={num}  holdout={holdout}")
    log("=" * 60)

    # ----- 1. Project ---------------------------------------------------
    if "project_id" not in entry:
        code, p = http("POST", "/projects", {
            "name": f"e2e-holdout-{task}-{datetime.now().strftime('%Y%m%d-%H%M')}",
            "task_type": task,
            "description": f"Session 25 HO.8 smoke — holdout flow for {task}.",
        })
        if code != 201:
            entry["project_error"] = (code, p)
            log("  project create FAILED", code, p)
            save_state(state)
            return
        entry["project_id"] = p["id"]
        log("  project_id =", entry["project_id"])
        save_state(state)

    project_id = entry["project_id"]

    # ----- 2. SDG generate with holdout_size ---------------------------
    if "parent_dataset_id" not in entry:
        body = sdg_body_for(task, project_id, num, holdout)
        code, r = http("POST", "/datasets/generate", body, timeout=60)
        if code != 202:
            entry["sdg_submit_error"] = (code, r)
            log("  SDG submit FAILED", code, r)
            save_state(state)
            return
        entry["parent_dataset_id"] = r["dataset_id"]
        entry["sdg_job_id"] = r["job_id"]
        log("  SDG submitted parent =", entry["parent_dataset_id"], "job =", entry["sdg_job_id"])
        save_state(state)

    parent_id = entry["parent_dataset_id"]

    if entry.get("sdg_status") not in ("ready", "completed"):
        def get_parent():
            c, d = http("GET", f"/datasets/{parent_id}")
            return d if c == 200 else None

        ds = wait_until(
            get_parent,
            lambda d: bool(d) and d.get("status") in ("ready", "completed", "failed"),
            f"SDG {task}",
            timeout_s=1800,
            interval_s=15,
        )
        if not ds or ds.get("status") not in ("ready", "completed"):
            entry["sdg_final"] = ds
            log("  SDG did not complete:", ds)
            save_state(state)
            return
        entry["sdg_status"] = ds.get("status")
        entry["parent_num_samples"] = ds.get("num_samples")
        entry["parent_metadata"] = ds.get("generation_metadata") or {}
        log("  SDG done — parent_num_samples =", entry["parent_num_samples"])
        save_state(state)

    # ----- 3. Locate the holdout child --------------------------------
    if "child_dataset_id" not in entry:
        meta = entry.get("parent_metadata", {})
        child_id = meta.get("holdout_dataset_id")
        if not child_id:
            log("  parent_metadata.holdout_dataset_id MISSING -- falling back to /datasets scan")
            code, lst = http("GET", f"/datasets?project_id={project_id}")
            if code == 200 and lst:
                items = lst.get("items") if isinstance(lst, dict) else lst
                children = [
                    d for d in (items or [])
                    if str(d.get("parent_dataset_id") or "") == str(parent_id)
                ]
                if children:
                    child_id = children[0]["id"]
        if not child_id:
            entry["holdout_missing"] = True
            log("  HOLDOUT child NOT FOUND -- aborting task_type")
            save_state(state)
            return
        entry["child_dataset_id"] = child_id
        log("  child_dataset_id =", child_id)
        # Inspect child to confirm parent_dataset_id wired
        code, child = http("GET", f"/datasets/{child_id}")
        if code == 200:
            entry["child_num_samples"] = child.get("num_samples")
            entry["child_parent_check"] = str(child.get("parent_dataset_id"))
            log("  child num_samples =", entry["child_num_samples"], "parent_link =", entry["child_parent_check"])
        save_state(state)

    # ----- 4. Train on PARENT ------------------------------------------
    if entry.get("train_status") not in ("completed",):
        if "training_id" not in entry:
            train_body = {
                "mode": "manual",
                "project_id": project_id,
                "dataset_id": parent_id,
                "base_model": BASE_MODEL,
                "training_name": f"e2e-holdout-{task}",
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
                    "lora": {"r": 16, "alpha": 32, "dropout": 0.05},
                },
            }
            code, t = http("POST", "/trainings", train_body, timeout=60)
            if code != 202:
                entry["train_submit_error"] = (code, t)
                log("  TRAIN submit FAILED", code, t)
                save_state(state)
                return
            entry["training_id"] = t["training_id"]
            log("  training_id =", entry["training_id"])
            save_state(state)

        training_id = entry["training_id"]

        def get_training():
            c, d = http("GET", f"/trainings/{training_id}")
            return d if c == 200 else None

        tj = wait_until(
            get_training,
            lambda d: bool(d) and d.get("status") in ("completed", "failed", "cancelled"),
            f"train {task}",
            timeout_s=3600,
            interval_s=20,
        )
        entry["train_status"] = (tj or {}).get("status")
        entry["train_error"] = (tj or {}).get("error_message")
        log("  TRAIN", entry["train_status"], "err=", entry["train_error"])
        save_state(state)
        if entry["train_status"] != "completed":
            return

    # ----- 5. Locate artifact ------------------------------------------
    if "artifact_id" not in entry:
        code, arts = http("GET", f"/models?training_job_id={entry['training_id']}")
        if code != 200 or not (arts or {}).get("items"):
            entry["artifact_error"] = (code, arts)
            log("  no artifact found", code, arts)
            save_state(state)
            return
        entry["artifact_id"] = arts["items"][0]["id"]
        log("  artifact_id =", entry["artifact_id"])
        save_state(state)

    # ----- 6. Export GGUF ---------------------------------------------
    if not entry.get("ollama_model_tag"):
        if "export_job_id" not in entry:
            code, x = http(
                "POST",
                f"/models/{entry['artifact_id']}/export",
                {"format": "gguf", "quantization": "q4_k_m"},
                timeout=60,
            )
            if code != 202:
                entry["export_submit_error"] = (code, x)
                log("  EXPORT submit FAILED", code, x)
                save_state(state)
                return
            entry["export_job_id"] = x.get("job_id")
            log("  export_job_id =", entry["export_job_id"])
            save_state(state)

        def get_art():
            c, d = http("GET", f"/models/{entry['artifact_id']}")
            return d if c == 200 else None

        art = wait_until(
            get_art,
            lambda d: bool(d) and (d.get("gguf_uri") or d.get("export_error_message")),
            f"export {task}",
            timeout_s=1800,
            interval_s=15,
        )
        entry["gguf_uri"] = (art or {}).get("gguf_uri")
        entry["ollama_model_tag"] = (art or {}).get("ollama_model_tag")
        entry["export_error"] = (art or {}).get("export_error_message")
        log("  EXPORT gguf_uri =", entry["gguf_uri"], "tag =", entry["ollama_model_tag"])
        save_state(state)
        if not entry.get("ollama_model_tag"):
            return

    # ----- 7. EVAL on CHILD (leak-free) --------------------------------
    if entry.get("eval_status") not in ("completed",):
        if "evaluation_id" not in entry:
            eval_body = {
                "model_artifact_id": entry["artifact_id"],
                "dataset_id": entry["child_dataset_id"],
                "use_llm_judge": True,
            }
            code, e = http("POST", "/evaluations", eval_body, timeout=60)
            if code != 202:
                entry["eval_submit_error"] = (code, e)
                log("  EVAL submit FAILED", code, e)
                save_state(state)
                return
            entry["evaluation_id"] = e["evaluation_id"]
            log("  eval_id =", entry["evaluation_id"])
            save_state(state)

        eval_id = entry["evaluation_id"]

        def get_eval():
            c, d = http("GET", f"/evaluations/{eval_id}")
            return d if c == 200 else None

        ev = wait_until(
            get_eval,
            lambda d: bool(d) and d.get("status") in ("completed", "failed", "cancelled"),
            f"eval {task}",
            timeout_s=2400,
            interval_s=15,
        )
        entry["eval_status"] = (ev or {}).get("status")
        entry["eval_metrics"] = (ev or {}).get("metrics_json")
        entry["llm_judge_score"] = (ev or {}).get("llm_judge_score")
        entry["eval_error"] = (ev or {}).get("error_message")
        log("  EVAL", entry["eval_status"], "metrics =", entry["eval_metrics"], "judge =", entry["llm_judge_score"])
        save_state(state)

    entry["ended"] = now_iso()
    save_state(state)


# ---------- summary -------------------------------------------------------


def summarize(state: dict) -> None:
    log("=" * 60)
    log("SUMMARY")
    log("=" * 60)
    for task, e in state.get("tasks", {}).items():
        ok_sdg = e.get("sdg_status") in ("ready", "completed")
        ok_holdout = bool(e.get("child_dataset_id"))
        ok_train = e.get("train_status") == "completed"
        ok_export = bool(e.get("ollama_model_tag"))
        ok_eval = e.get("eval_status") == "completed"
        line = (
            f"  {task:14s}  sdg={'OK' if ok_sdg else 'X'}  "
            f"holdout={'OK' if ok_holdout else 'X'}({e.get('child_num_samples', '-')})  "
            f"train={'OK' if ok_train else 'X'}  "
            f"export={'OK' if ok_export else 'X'}  "
            f"eval={'OK' if ok_eval else 'X'}  "
            f"judge={e.get('llm_judge_score', '-')}"
        )
        log(line)


# ---------- entrypoint ----------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--task",
        choices=["classification", "tool_calling", "qa", "all"],
        default="all",
        help="Which task_type to run (default: all 3 sequentially).",
    )
    ap.add_argument("--num", type=int, default=100, help="num_samples (train target).")
    ap.add_argument("--holdout", type=int, default=20, help="holdout_size (>=0).")
    args = ap.parse_args()

    log(f"START  task={args.task}  num={args.num}  holdout={args.holdout}")

    # Pre-flight: API up?
    code, h = http("GET", "/../health")  # /health lives outside /api/v1
    if code != 200:
        log("API health FAILED — is the stack up?", code, h)
        return

    state = load_state()
    tasks = ["classification", "tool_calling", "qa"] if args.task == "all" else [args.task]
    for t in tasks:
        run_task_type(t, args.num, args.holdout, state)

    summarize(state)
    log("=== DONE ===")


if __name__ == "__main__":
    main()
