"""§11 SafeTensors export + download + §12 legacy /completions smoke.

Covers leftover endpoints not exercised by §16 (which only did GGUF):
  T1  POST /api/v1/models/{id}/export       safetensors
  T2  poll GET /api/v1/models/{id}          until safetensors_uri set
  T3  GET  /api/v1/models/{id}/download     stream binary, verify >0 bytes
  T4  POST /api/v1/inference/completions    legacy text endpoint (non-chat)

Usage:
    python3 swagger_smoke_section11_12.py --artifact-id <UUID> [--base-url ...]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from typing import Any, Callable

import httpx

LOG_PATH = "/tmp/logs/section11_12.log"


class Recorder:
    def __init__(self) -> None:
        self.results: list[tuple[str, str, str]] = []

    def record(self, task: str, status: str, detail: str = "") -> None:
        line = f"[§11/12] {task} {status} {detail}".rstrip()
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


def run(base_url: str, artifact_id: str) -> int:
    rec = Recorder()
    state: dict[str, Any] = {}

    # T1 SafeTensors export
    def t1() -> None:
        r = http(
            "POST",
            f"{base_url}/api/v1/models/{artifact_id}/export",
            json={"format": "safetensors"},
        )
        assert r.status_code == 202, f"status={r.status_code} body={r.text[:300]}"
        body = r.json()
        state["export_job_id"] = body.get("job_id")
        rec.record(
            "T1 safetensors export submit",
            "PASS",
            f"job_id={body.get('job_id')}",
        )

    safe(rec, "T1 safetensors export submit", t1)

    # T2 poll until safetensors_uri set
    def t2() -> None:
        deadline = time.time() + 360  # 6 min
        while time.time() < deadline:
            r = http("GET", f"{base_url}/api/v1/models/{artifact_id}")
            assert r.status_code == 200, f"status={r.status_code}"
            body = r.json()
            err = body.get("export_error_message")
            st_uri = body.get("safetensors_uri")
            if err:
                raise RuntimeError(f"export failed: {err[:300]}")
            if st_uri:
                state["safetensors_uri"] = st_uri
                rec.record("T2 safetensors completed", "PASS", f"uri={st_uri}")
                return
            time.sleep(5)
        raise TimeoutError("safetensors export did not complete in 6 min")

    safe(rec, "T2 safetensors completed", t2)

    # T3 download stream
    def t3() -> None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as fh:
            tmp_path = fh.name
        try:
            with httpx.Client(timeout=120.0) as c:
                with c.stream("GET", f"{base_url}/api/v1/models/{artifact_id}/download") as r:
                    assert r.status_code == 200, f"status={r.status_code}"
                    bytes_total = 0
                    with open(tmp_path, "wb") as out:
                        for chunk in r.iter_bytes(chunk_size=64 * 1024):
                            out.write(chunk)
                            bytes_total += len(chunk)
            assert bytes_total > 0, "downloaded 0 bytes"
            # quick sanity — should be at least 1 KB
            assert bytes_total >= 1024, f"download too small: {bytes_total} bytes"
            rec.record(
                "T3 download stream",
                "PASS",
                f"bytes={bytes_total} ({bytes_total/1024:.1f} KB)",
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    safe(rec, "T3 download stream", t3)

    # T4 legacy /completions
    def t4() -> None:
        r = http(
            "POST",
            f"{base_url}/api/v1/inference/completions",
            json={
                "model": artifact_id,
                "prompt": "The capital of France is",
                "max_tokens": 20,
                "temperature": 0.0,
            },
            timeout=120.0,
        )
        assert r.status_code == 200, f"status={r.status_code} body={r.text[:300]}"
        body = r.json()
        choices = body.get("choices") or []
        assert choices, f"no choices: {body}"
        text = choices[0].get("text") or ""
        rec.record(
            "T4 legacy completions",
            "PASS",
            f"text={text[:80]!r} usage={body.get('usage')}",
        )

    safe(rec, "T4 legacy completions", t4)

    passed, failed = rec.summary()
    print("=" * 60)
    print(f"§11/12 SUMMARY: {passed} PASS, {failed} FAIL")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--artifact-id", required=True)
    args = p.parse_args()
    os.makedirs("/tmp/logs", exist_ok=True)
    open(LOG_PATH, "w").close()
    sys.exit(run(args.base_url, args.artifact_id))
