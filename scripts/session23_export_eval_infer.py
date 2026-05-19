"""Followup pass: EXPORT(gguf) -> wait -> EVAL -> wait -> INFERENCE per artifact.

Reads /tmp/e2e-tool-calling/state.json to find artifact_ids from the train pass.
Fixes 2 bugs in session21_e2e_resume.py:
  1. export body uses field name `format` not `export_format` (API 422 otherwise)
  2. EVAL is run AFTER export, not before, because eval_service requires the
     artifact to have an ollama_model_tag (set only after gguf export).
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


def wait_until(getter, predicate, label: str, *, timeout_s: int, interval_s: int = 15):
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        last = getter()
        if predicate(last):
            return last
        time.sleep(interval_s)
    log(f"TIMEOUT [{label}] after {timeout_s}s")
    return last


def main() -> None:
    state = json.loads(STATE_FILE.read_text())
    # Train dataset stays under `train_dataset_id` (the 1000-row SDG the models
    # were fine-tuned on). Eval uses `test_dataset_id` — a separate held-out set
    # generated specifically for unbiased measurement (no leakage).
    # Fall back to legacy `dataset_id` if those split fields aren't set yet.
    train_dataset_id = state.get("train_dataset_id") or state.get("dataset_id")
    test_dataset_id = state.get("test_dataset_id") or state.get("dataset_id")
    models = state.get("models", [])

    log("=== FOLLOWUP: export -> eval -> inference ===")
    log("train_dataset_id =", train_dataset_id)
    log("test_dataset_id  =", test_dataset_id, "(used for EVAL)")
    log("n_models =", len(models))

    def save():
        STATE_FILE.write_text(json.dumps(state, indent=2, default=str))

    for entry in models:
        nick = entry.get("nick", "?")
        artifact_id = entry.get("artifact_id")
        if not artifact_id:
            log("SKIP", nick, "- no artifact_id (train likely failed)")
            continue

        log("=" * 60)
        log("MODEL:", nick, "artifact=", artifact_id)
        log("=" * 60)

        # ---- EXPORT GGUF (idempotent: skip if already exported) ----
        if entry.get("gguf_uri"):
            log("  EXPORT: already done — gguf_uri set, skipping")
        else:
            entry.pop("export_error_submit", None)
            entry.pop("export_error_message", None)
            c, x = http(
                "POST",
                f"/models/{artifact_id}/export",
                {"format": "gguf", "quantization": "q4_k_m"},
                timeout=60,
            )
            if c != 202:
                log("  EXPORT submit FAILED", c, x)
                entry["export_error_submit"] = x
                save()
                continue
            entry["export_job_id"] = x.get("job_id")
            log("  export submitted job_id=", entry["export_job_id"])
            save()

            def get_artifact(_aid=artifact_id):
                c2, d = http("GET", f"/models/{_aid}")
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
            log(
                "  EXPORT gguf=", "yes" if entry.get("gguf_uri") else "no",
                "tag=", entry.get("ollama_model_tag"),
                "err=", entry.get("export_error_message"),
            )
            save()

        # ---- EVAL ----
        if entry.get("eval_status") == "completed":
            log("  EVAL: already completed — skipping")
        elif not entry.get("ollama_model_tag"):
            log("  EVAL skipped — no ollama tag (export must have failed)")
        else:
            entry.pop("eval_error_submit", None)
            eval_body = {
                "model_artifact_id": artifact_id,
                "dataset_id": test_dataset_id,
                "use_llm_judge": True,
            }
            c, e = http("POST", "/evaluations", eval_body, timeout=60)
            if c != 202:
                log("  EVAL submit FAILED", c, e)
                entry["eval_error_submit"] = e
                save()
            else:
                eval_id = e["evaluation_id"]
                entry["evaluation_id"] = eval_id
                log("  eval submitted evaluation_id=", eval_id)
                save()

                def get_eval(_eid=eval_id):
                    c2, d = http("GET", f"/evaluations/{_eid}")
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
                log(
                    "  EVAL", entry["eval_status"],
                    "judge=", entry.get("llm_judge_score"),
                    "metrics=", entry.get("eval_metrics"),
                )
                save()

        # ---- INFERENCE ----
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
                entry["inference_response"] = (
                    resp["choices"][0]["message"]["content"] if c == 200 else resp
                )
            except Exception:
                entry["inference_response"] = resp
            log(
                "  INFERENCE code=", c,
                "resp[0..200]=", str(entry.get("inference_response"))[:200],
            )
            save()

        entry["followup_ended"] = datetime.now(timezone.utc).isoformat()
        save()

    log("=== FOLLOWUP DONE ===")


if __name__ == "__main__":
    main()
