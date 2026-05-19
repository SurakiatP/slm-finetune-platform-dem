"""Node 9 verify — submit a NEW rule-based eval and dump full response.

Run AFTER session25_holdout_e2e.py has prepared the artifact + child dataset:

    python scripts/session25_holdout_e2e.py --task classification --num 40 --holdout 20
    python scripts/verify_node_9_eval_rulebased.py

Reads /tmp/e2e-holdout/state.json to find (artifact_id, child_dataset_id),
POSTs /evaluations with use_llm_judge=false, polls until completed,
dumps the full GET /evaluations/{id} response to:

    /tmp/node-9-verify/fresh-eval-rulebased.json

Plus a small `contract_diff.txt` that lists which keys differ from the
Session 26 baseline at:

    /root/slm-platform/tests/fixtures/baseline/node-9/session-26-2026-05-18_eval-final.json

Numeric metric values are NOT compared (LoRA init is random). Only the
SHAPE contract — top-level keys, metrics_json sub-keys, types — is asserted.

Exit 0 on contract pass, 1 on contract fail.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "http://localhost:8000/api/v1"
SETUP_STATE = Path("/tmp/e2e-holdout/state.json")
OUT_DIR = Path("/tmp/node-9-verify")
BASELINE = Path(
    "/root/slm-platform/tests/fixtures/baseline/node-9/"
    "session-26-2026-05-18_eval-final.json"
)


def http(method: str, path: str, body: dict | None = None, timeout: int = 60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path,
        method=method,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def wait_eval(eval_id: str, timeout_s: int = 600, interval_s: int = 5) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        code, data = http("GET", f"/evaluations/{eval_id}")
        if code == 200 and data.get("status") in ("completed", "failed", "cancelled"):
            return data
        time.sleep(interval_s)
    raise TimeoutError(f"eval {eval_id} did not finish in {timeout_s}s")


def diff_contract(fresh: dict, baseline: dict) -> list[str]:
    """Return human-readable list of contract violations."""
    issues: list[str] = []
    fresh_keys = set(fresh.keys())
    baseline_keys = set(baseline.keys())
    missing = baseline_keys - fresh_keys
    extra = fresh_keys - baseline_keys
    if missing:
        issues.append(f"missing top-level keys: {sorted(missing)}")
    if extra:
        issues.append(f"unexpected top-level keys: {sorted(extra)}")

    # status must be "completed"
    if fresh.get("status") != "completed":
        issues.append(f"status != completed (got {fresh.get('status')!r})")

    # metrics_json sub-keys (classification)
    fresh_m = fresh.get("metrics_json") or {}
    base_m = baseline.get("metrics_json") or {}
    fresh_mk = set(fresh_m.keys())
    base_mk = set(base_m.keys())
    m_missing = base_mk - fresh_mk
    m_extra = fresh_mk - base_mk
    if m_missing:
        issues.append(f"metrics_json missing sub-keys: {sorted(m_missing)}")
    if m_extra:
        issues.append(f"metrics_json unexpected sub-keys: {sorted(m_extra)}")

    # llm_judge_score must be null for rule-based
    if fresh.get("llm_judge_score") is not None:
        issues.append(
            f"llm_judge_score is not null (got {fresh.get('llm_judge_score')!r}); "
            "expected null for rule-based eval"
        )

    # Type-check: n must be int, accuracy/f1_macro must be float-ish,
    # confusion_matrix must be list of lists
    n = fresh_m.get("n")
    if not isinstance(n, int):
        issues.append(f"metrics_json.n type: expected int, got {type(n).__name__}")
    for k in ("accuracy", "f1_macro"):
        v = fresh_m.get(k)
        if not isinstance(v, (int, float)):
            issues.append(
                f"metrics_json.{k} type: expected number, got {type(v).__name__}"
            )
    cm = fresh_m.get("confusion_matrix")
    if not (isinstance(cm, list) and all(isinstance(r, list) for r in cm)):
        issues.append("metrics_json.confusion_matrix not list-of-list")

    return issues


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not SETUP_STATE.exists():
        print(f"setup state not found at {SETUP_STATE}; run session25_holdout_e2e.py first")
        return 1
    state = json.loads(SETUP_STATE.read_text())
    cls = state.get("tasks", {}).get("classification") or {}
    artifact_id = cls.get("artifact_id")
    child_id = cls.get("child_dataset_id")
    if not artifact_id or not child_id:
        print(f"setup state missing artifact_id or child_dataset_id: {cls}")
        return 1

    print(f"submit eval: artifact={artifact_id} dataset={child_id} use_llm_judge=false")
    code, resp = http(
        "POST",
        "/evaluations",
        {
            "model_artifact_id": artifact_id,
            "dataset_id": child_id,
            "use_llm_judge": False,
        },
        timeout=60,
    )
    if code != 202:
        print(f"FAIL: submit returned {code}: {resp}")
        return 1
    eval_id = resp["evaluation_id"]
    print(f"  eval_id = {eval_id}")

    print("polling for completion...")
    final = wait_eval(eval_id, timeout_s=600, interval_s=5)

    fresh_path = OUT_DIR / "fresh-eval-rulebased.json"
    fresh_path.write_text(json.dumps(final, indent=2, default=str))
    print(f"dumped fresh response to {fresh_path}")

    if not BASELINE.exists():
        print(f"baseline not found at {BASELINE}; skipping contract diff")
        return 0
    baseline = json.loads(BASELINE.read_text())

    issues = diff_contract(final, baseline)
    diff_path = OUT_DIR / "contract_diff.txt"
    if not issues:
        diff_path.write_text("CONTRACT PASS — no shape/key violations\n")
        print("\n=== CONTRACT PASS ===")
        print("All top-level keys present, status=completed, metrics_json shape matches,")
        print("llm_judge_score=null as expected for rule-based.")
        return 0
    diff_path.write_text(
        "CONTRACT FAIL — refactor changed the eval response shape:\n\n"
        + "\n".join(f"  • {i}" for i in issues)
        + "\n"
    )
    print("\n=== CONTRACT FAIL ===")
    for i in issues:
        print(f"  • {i}")
    print(f"\nSee {diff_path}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
