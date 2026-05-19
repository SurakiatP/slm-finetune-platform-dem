"""Patch /tmp/e2e-tool-calling/state.json for the test-set rerun:

- add train_dataset_id (the 1000-row SDG the models were trained on)
- add test_dataset_id (the 100-row holdout we just generated)
- clear stale eval/error fields so the followup re-evaluates on test set
- preserve gguf_uri / ollama_model_tag / artifact_id (real artifacts on disk)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

STATE = Path("/tmp/e2e-tool-calling/state.json")
TRAIN_DS = "c4ad537c-5c44-4841-a369-c6f8a3117e29"  # 1000-row, used for training
TEST_DS = "0d6b1ed3-2932-45d4-9d2a-7b54a0f3cb01"   # 100-row holdout for eval

# Stale eval-only fields to clear on every model so the followup re-evaluates
EVAL_FIELDS_TO_CLEAR = (
    "evaluation_id",
    "eval_status",
    "eval_metrics",
    "llm_judge_score",
    "eval_error_message",
    "eval_error_submit",
)
# Stale export-error fields (kept gguf_uri / ollama_model_tag if set)
EXPORT_ERROR_FIELDS_TO_CLEAR = ("export_error_submit",)


def main() -> None:
    d = json.loads(STATE.read_text())

    d["train_dataset_id"] = TRAIN_DS
    d["test_dataset_id"] = TEST_DS
    d["test_set_patched_at"] = datetime.now(timezone.utc).isoformat()

    for entry in d.get("models", []):
        # Preserve Llama-1B's biased eval result for the report — move under a
        # namespaced field rather than dropping it.
        if entry.get("nick") == "Llama-3.2-1B" and entry.get("eval_status") == "completed":
            entry["eval_biased_on_trainset"] = {
                "evaluation_id": entry.get("evaluation_id"),
                "eval_metrics": entry.get("eval_metrics"),
                "llm_judge_score": entry.get("llm_judge_score"),
                "note": "Eval on the 1000-row TRAIN set; biased — kept for comparison only.",
            }
        for k in EVAL_FIELDS_TO_CLEAR:
            entry.pop(k, None)
        for k in EXPORT_ERROR_FIELDS_TO_CLEAR:
            entry.pop(k, None)

    STATE.write_text(json.dumps(d, indent=2, default=str))
    print("PATCHED")
    print("train_dataset_id =", d["train_dataset_id"])
    print("test_dataset_id  =", d["test_dataset_id"])
    for m in d["models"]:
        nick = m.get("nick")
        gguf = "y" if m.get("gguf_uri") else "-"
        ollama = m.get("ollama_model_tag") or "-"
        art = m.get("artifact_id", "?")[:8]
        print(f"  {nick:14}  artifact={art}  gguf={gguf}  ollama={ollama}")


if __name__ == "__main__":
    main()
