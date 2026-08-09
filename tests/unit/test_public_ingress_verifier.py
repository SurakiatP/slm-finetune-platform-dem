"""Guards for `scripts/verify_public_ingress.sh` and its runbook.

This script is the first thing that exercises the path a browser takes
(Cloudflare → edge → api) instead of the loopback path everything else has
been verified on. It runs exactly once, by a human, at the moment the tunnel
opens — so a check that silently stops checking would not be noticed by
anyone, ever. Hence guards.

Two properties matter most and are asserted structurally rather than by
substring:

  * the negative group (D) must not be able to pass because the host is
    unreachable — "nothing leaked" and "nothing responded" look identical
    from a curl exit code, and this repo has shipped that shape of false
    pass repeatedly (round-3.5's S4: a query that could not fail);
  * the endpoint sweep must cover the endpoints the frontend actually
    calls, cross-checked against the handoff doc rather than hardcoded
    twice and allowed to drift apart.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "verify_public_ingress.sh"
_RUNBOOK = _REPO / "docs" / "runbooks" / "public_ingress.md"


def _script() -> str:
    return _SCRIPT.read_text(encoding="utf-8")


def _script_lines() -> list[str]:
    return _script().splitlines()


def _runbook() -> str:
    return _RUNBOOK.read_text(encoding="utf-8")


def _find_line(needle: str, *, lines: list[str] | None = None) -> int:
    lines = lines if lines is not None else _script_lines()
    hits = [i for i, line in enumerate(lines) if needle in line]
    assert hits, f"`{needle}` not found in {_SCRIPT.name} — non-vacuity backstop"
    assert len(hits) == 1, (
        f"`{needle}` appears {len(hits)} times; these guards assume one "
        "occurrence — update them deliberately, not by deletion"
    )
    return hits[0]


# ---- the false-pass guard ----------------------------------------------------


def test_exposure_checks_are_skipped_when_the_host_is_unreachable() -> None:
    """`/docs` and `/metrics` must not be evaluated when group A failed.

    Against a dead host both return empty, both greps miss, and both report
    "not exposed" — a green light produced by nothing being up. The script
    must gate them on `health_code == 200`.
    """
    lines = _script_lines()
    guard = _find_line('if [[ "$health_code" != "200" ]]; then', lines=lines)

    docs = _find_line("$PUBLIC_API_URL/docs", lines=lines)
    metrics = _find_line("$PUBLIC_API_URL/metrics", lines=lines)
    assert guard < docs and guard < metrics, (
        "the /docs and /metrics exposure checks are no longer inside the "
        "health-gated branch — against an unreachable host they would report "
        "'nothing leaked' when the truth is 'nothing responded'"
    )
    # …and the branch must actually skip rather than fall through silently.
    branch = "\n".join(lines[guard : min(guard + 8, len(lines))])
    assert "SKIP" in branch.upper(), (
        "the unreachable-host branch no longer announces that it skipped — a "
        "silent skip reads as a pass in the summary"
    )


def test_curl_helper_does_not_concatenate_a_second_failure_code() -> None:
    """`curl -w '%{http_code}'` already prints 000 on a connection failure and
    exits non-zero; a trailing `|| echo 000` produces "000000", which matches
    no expectation and makes every comparison silently wrong. Found by
    smoke-running the script, not by reading it."""
    body = _script()
    assert not re.search(r"%\{http_code\}'.*\|\|\s*echo", body), (
        "code_for is appending a fallback to curl's own output again — the "
        "'000000' bug. Capture the output first, then default it."
    )


def test_every_check_group_feeds_the_failure_counter() -> None:
    """A group whose failures call `note` instead of `bad` cannot turn the
    run red. Only the known-outage cases are allowed to be advisory."""
    body = _script()
    assert body.count("FAIL=$((FAIL + 1))") == 1, (
        "the failure counter is incremented in more than one place — it "
        "belongs to bad() alone, or a group can be made advisory by accident"
    )
    assert re.search(r'if \[\[ "\$FAIL" -gt 0 \]\]', body) and "exit 1" in body, (
        "the script no longer exits non-zero on failures — a red run would be "
        "reported as success by any wrapper checking the exit code"
    )


# ---- coverage of what the frontend needs -------------------------------------

# The endpoints the 12 wired screens depend on. Kept explicit so adding a
# screen forces a deliberate edit here, and cross-checked against the
# script below.
_REQUIRED_ENDPOINTS = [
    "/api/v1/projects",
    "/api/v1/datasets",
    "/api/v1/trainings",
    "/api/v1/models",
    "/api/v1/evaluations",
    "/api/v1/base-models",
    "/api/v1/tasks",
    "/api/v1/usage",
    "/api/v1/inference/models",
]


@pytest.mark.parametrize("endpoint", _REQUIRED_ENDPOINTS)
def test_script_probes_every_endpoint_the_wired_screens_need(endpoint: str) -> None:
    assert endpoint in _script(), (
        f"{endpoint} is not probed by the ingress verifier, but the "
        "frontend's wired screens call it — a public deployment could be "
        "handed over with that route unreachable through the edge"
    )


def test_websocket_upgrade_is_probed_and_a_non_upgrade_fails() -> None:
    """TrainingMonitor's live progress dies silently if the upgrade does not
    survive Cloudflare + the edge — no error, just no frames. The probe must
    exist and its fall-through case must be `bad`, not a note."""
    body = _script()
    assert "Upgrade: websocket" in body, "the WebSocket upgrade probe is gone"
    match = re.search(r"case \"\$ws_probe\" in(.+?)esac", body, re.S)
    assert match, "the WebSocket probe no longer branches on the response"
    arms = match.group(1)
    default_arm = arms.rsplit(";;", 2)[-2] if arms.count(";;") >= 2 else arms
    assert "bad " in default_arm, (
        "the WebSocket probe's catch-all arm no longer calls bad() — a proxy "
        "that never upgrades would be reported as fine"
    )


@pytest.mark.parametrize(
    "port,service",
    [("5432", "PostgreSQL"), ("6379", "Redis"), ("9000", "MinIO"), ("11434", "Ollama")],
)
def test_negative_group_probes_the_infra_ports(port: str, service: str) -> None:
    """The P0 acceptance criterion is that an external scan reaches only the
    intended services. These are the ports that would prove otherwise."""
    assert f'"{port}:' in _script(), (
        f"{service} port {port} is no longer probed by the negative group — a "
        "publicly reachable infra port would go unnoticed"
    )


def test_docs_check_compares_bodies_not_status_codes() -> None:
    """The edge falls through to the SPA, so /docs legitimately returns 200.
    A status-code check here would fail every correct deployment (and a lazy
    fix would then delete the check)."""
    body = _script()
    assert "docs_body" in body and "root_body" in body, (
        "the decision-#12 check no longer compares response bodies"
    )
    assert re.search(r"docs_body.*grep -qi 'swagger", body), (
        "the /docs check no longer looks for Swagger markers in the body"
    )


# ---- script ↔ runbook consistency --------------------------------------------


def test_runbook_documents_every_env_var_the_script_requires() -> None:
    """A runbook that omits a required variable sends the operator into a
    `:?` abort at the worst possible moment — mid-cutover."""
    # Comment lines must be excluded: the script explains the `${VAR:?word}`
    # apostrophe/parenthesis parser trap in prose, and a naive scan reads
    # that literal `VAR` as a required variable. (Caught by this very test on
    # its first run — leaving the note so the filter is not "simplified" out.)
    code = "\n".join(
        line for line in _script_lines() if not line.lstrip().startswith("#")
    )
    required = set(re.findall(r"\$\{([A-Z_]+):\?", code))
    assert required, "no required (:?) variables found — parser or script changed"
    runbook = _runbook()
    missing = sorted(v for v in required if v not in runbook)
    assert not missing, (
        f"{missing} are required by the script but never named in the runbook"
    )


def test_runbook_warns_that_cors_replaces_rather_than_merges() -> None:
    """The specific mistake this deployment is set up to make: `.env` already
    carries a stale Lovable origin, and setting the variable drops the code
    defaults instead of adding to them."""
    runbook = _runbook().lower()
    assert "api_cors_origins" in runbook, "the runbook no longer names the CORS variable"
    assert "replaces" in runbook and "merge" in runbook, (
        "the runbook no longer states that API_CORS_ORIGINS replaces the "
        "default list rather than merging with it — the failure mode is a "
        "working curl and a broken browser app"
    )


def test_runbook_says_to_run_it_from_outside_the_box() -> None:
    """Run it on the box and the negative group passes via loopback."""
    assert re.search(r"from a laptop, not from the box", _runbook(), re.I), (
        "the runbook no longer tells the operator to run the verifier from "
        "outside — group D would pass for the wrong reason"
    )


def test_runbook_has_an_empty_verification_log_table() -> None:
    section = _runbook().split("## Verification log", 1)
    assert len(section) == 2, "the runbook lost its Verification log section"
    assert "| Date | Operator |" in section[1], "the verification log lost its table header"
