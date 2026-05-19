"""§13 Evaluation smoke driver — rule-based + (optional) LLM judge + compare.

Drives Swagger §13 against existing artifact + dataset:
  T1  POST /api/v1/evaluations                  rule-based (use_llm_judge=false)
  T2  poll GET /api/v1/evaluations/{id}         until completed
  T3  GET  /api/v1/evaluations/{id}             verify metrics_json populated
  T4  POST /api/v1/evaluations                  second run (for compare)
  T5  poll second eval
  T6  POST /api/v1/evaluations/compare          [eval1, eval2]
  T7  (--with-llm-judge) POST /api/v1/evaluations  rule + LLM judge
  T8  (--with-llm-judge) poll                       verify llm_judge_score not None

Usage:
    python3 swagger_smoke_section13.py --artifact-id <UUID> --dataset-id <UUID>
                                       [--with-llm-judge] [--judge-model anthropic/claude-3.5-sonnet]

Exit 0 = all PASS, 1 = any FAIL. Log → /tmp/logs/section13.log.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Callable

import httpx

LOG_PATH = "/tmp/logs/section13.log"


class Recorder:
    def __init__(self) -> None:
        self.results: list[tuple[str, str, str]] = []

    def record(self, task: str, status: str, detail: str = "") -> None:
        line = f"[§13] {task} {status} {detail}".rstrip()
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


def submit_eval(
    base_url: str,
    artifact_id: str,
    dataset_id: str,
    use_judge: bool = False,
    judge_model: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model_artifact_id": artifact_id,
        "dataset_id": dataset_id,
        "use_llm_judge": use_judge,
    }
    if use_judge and judge_model:
        payload["judge_model"] = judge_model
    r = http("POST", f"{base_url}/api/v1/evaluations", json=payload)
    if r.status_code != 202:
        raise RuntimeError(f"submit failed: status={r.status_code} body={r.text[:300]}")
    return r.json()


def poll_eval(base_url: str, eval_id: str, timeout_s: int = 240) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    last_status = None
    while time.time() < deadline:
        r = http("GET", f"{base_url}/api/v1/evaluations/{eval_id}")
        if r.status_code != 200:
            raise RuntimeError(f"poll failed: status={r.status_code}")
        body = r.json()
        status = body.get("status")
        if status != last_status:
            print(f"   [poll {eval_id[:8]}] status={status}", flush=True)
            last_status = status
        if status in {"completed", "failed", "cancelled"}:
            if status != "completed":
                err = body.get("error_message", "<none>")
                raise RuntimeError(f"eval {status}: {err[:300]}")
            return body
        time.sleep(5)
    raise TimeoutError(f"eval {eval_id} did not complete within {timeout_s}s")


def run(
    base_url: str,
    artifact_id: str,
    dataset_id: str,
    with_llm_judge: bool,
    judge_model: str,
) -> int:
    rec = Recorder()
    state: dict[str, Any] = {}

    # T1 submit rule-based
    def t1() -> None:
        body = submit_eval(base_url, artifact_id, dataset_id, use_judge=False)
        state["eval1_id"] = body["evaluation_id"]
        state["eval1_job"] = body["job_id"]
        rec.record("T1 submit rule-based", "PASS", f"eval_id={body['evaluation_id']}")

    safe(rec, "T1 submit rule-based", t1)

    # T2 poll first eval
    def t2() -> None:
        if not state.get("eval1_id"):
            raise RuntimeError("no eval1_id (T1 failed)")
        body = poll_eval(base_url, state["eval1_id"], timeout_s=240)
        state["eval1_body"] = body
        rec.record(
            "T2 eval1 completed",
            "PASS",
            f"ended_at={body.get('ended_at')}",
        )

    safe(rec, "T2 eval1 completed", t2)

    # T3 verify metrics
    def t3() -> None:
        body = state.get("eval1_body")
        if not body:
            raise RuntimeError("no eval1_body")
        metrics = body.get("metrics_json") or {}
        assert metrics, f"metrics_json empty/null: {body}"
        # For QA expect at least one of: bleu, rouge_l, exact_match
        keys = set(metrics.keys())
        expected_qa = {"bleu", "rouge_l", "rouge_1", "rouge_2", "exact_match"}
        intersect = keys & expected_qa
        assert intersect, f"no QA metrics found in {keys}"
        # llm_judge_score should be None for rule-based
        assert body.get("llm_judge_score") is None, (
            f"llm_judge_score not None for rule-based: {body.get('llm_judge_score')}"
        )
        rec.record(
            "T3 metrics shape",
            "PASS",
            f"keys={sorted(keys)} judge_score=None",
        )

    safe(rec, "T3 metrics shape", t3)

    # T4 second eval (for compare)
    def t4() -> None:
        body = submit_eval(base_url, artifact_id, dataset_id, use_judge=False)
        state["eval2_id"] = body["evaluation_id"]
        rec.record("T4 submit eval2", "PASS", f"eval_id={body['evaluation_id']}")

    safe(rec, "T4 submit eval2", t4)

    # T5 poll second
    def t5() -> None:
        if not state.get("eval2_id"):
            raise RuntimeError("no eval2_id (T4 failed)")
        body = poll_eval(base_url, state["eval2_id"], timeout_s=240)
        state["eval2_body"] = body
        rec.record("T5 eval2 completed", "PASS", f"ended_at={body.get('ended_at')}")

    safe(rec, "T5 eval2 completed", t5)

    # T6 compare
    def t6() -> None:
        e1 = state.get("eval1_id")
        e2 = state.get("eval2_id")
        if not (e1 and e2):
            raise RuntimeError("missing eval ids (T1/T4 failed)")
        r = http(
            "POST",
            f"{base_url}/api/v1/evaluations/compare",
            json={"evaluation_ids": [e1, e2]},
        )
        assert r.status_code == 200, f"status={r.status_code} body={r.text[:300]}"
        body = r.json()
        ids = body.get("evaluation_ids") or []
        metrics = body.get("metrics") or {}
        assert len(ids) == 2, f"expected 2 ids, got {ids}"
        assert metrics, f"metrics empty: {body}"
        # Each metric should map to a dict {eval_id: value}
        sample_metric = next(iter(metrics))
        sample_inner = metrics[sample_metric]
        assert isinstance(sample_inner, dict), f"metric inner not dict: {sample_inner}"
        rec.record(
            "T6 compare",
            "PASS",
            f"metrics={sorted(metrics.keys())} judge_scores_keys={sorted((body.get('judge_scores') or {}).keys())[:2]}",
        )

    safe(rec, "T6 compare", t6)

    # T7/T8 LLM judge
    if with_llm_judge:
        def t7() -> None:
            body = submit_eval(
                base_url,
                artifact_id,
                dataset_id,
                use_judge=True,
                judge_model=judge_model,
            )
            state["eval3_id"] = body["evaluation_id"]
            rec.record(
                "T7 submit LLM judge",
                "PASS",
                f"eval_id={body['evaluation_id']} judge={judge_model}",
            )

        safe(rec, "T7 submit LLM judge", t7)

        def t8() -> None:
            if not state.get("eval3_id"):
                raise RuntimeError("no eval3_id")
            body = poll_eval(base_url, state["eval3_id"], timeout_s=300)
            score = body.get("llm_judge_score")
            judge_used = body.get("llm_judge_model")
            metrics = body.get("metrics_json") or {}
            n = metrics.get("n", 0)
            skipped = metrics.get("llm_judge_skipped_rows", 0)
            assert score is not None, (
                f"llm_judge_score is None — likely judge model failed for all {n} rows "
                f"(skipped={skipped}). Pick a model from `python /tmp/probe_judge.py`."
            )
            assert isinstance(score, (int, float)), f"score not numeric: {score}"
            assert skipped < n, f"all {n} rows skipped — judge unusable"
            rec.record(
                "T8 LLM judge score",
                "PASS",
                f"score={score:.3f} model={judge_used} skipped={skipped}/{n}",
            )

        safe(rec, "T8 LLM judge score", t8)
    else:
        rec.record("T7 LLM judge", "SKIP", "(--with-llm-judge not set)")
        rec.record("T8 LLM judge score", "SKIP", "(--with-llm-judge not set)")

    passed, failed = rec.summary()
    skipped = sum(1 for _, s, _ in rec.results if s == "SKIP")
    print("=" * 60)
    print(f"§13 SUMMARY: {passed} PASS, {failed} FAIL, {skipped} SKIP")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--artifact-id", required=True)
    p.add_argument("--dataset-id", required=True)
    p.add_argument("--with-llm-judge", action="store_true")
    p.add_argument("--judge-model", default="google/gemini-3.1-flash-lite-preview")
    args = p.parse_args()

    os.makedirs("/tmp/logs", exist_ok=True)
    open(LOG_PATH, "w").close()
    sys.exit(
        run(
            args.base_url,
            args.artifact_id,
            args.dataset_id,
            args.with_llm_judge,
            args.judge_model,
        )
    )
