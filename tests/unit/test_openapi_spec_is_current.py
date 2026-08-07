"""The committed `openapi.json` must match the app it claims to describe.

`scripts/export_openapi.py` instructs "Regenerate this whenever the API
contract changes", and that instruction is the entire sync mechanism — which
means the spec is only ever as current as someone's memory. It has already
failed twice: `docs/openapi.json` existed as a hand-copied mirror and drifted
(deleted in this commit), and the root file itself sat a route behind after
`/ready` was added.

That matters more here than in most projects, because this spec is not a
by-product. `docs/04-frontend-integration-smart-model-tune.md` hands it to the
`smart-model-tune` team as *the* machine-readable contract, and that team
works in a different repo with no way to tell a stale spec from a current one.
A missing route reads to them as "the backend does not support this".

So the check is mechanical rather than remembered: regenerate in memory,
compare to the file on disk, and name the exact fix in the failure message.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = _REPO_ROOT / "openapi.json"

_REGENERATE = (
    "Run:\n"
    "  DATABASE_URL=postgresql+asyncpg://x:x@localhost/x "
    "python scripts/export_openapi.py\n"
    "and commit the result."
)


def _committed() -> dict:
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def _live() -> dict:
    # Imported inside the test so a failure to build the app surfaces here as
    # a test failure rather than a collection error in an unrelated file.
    from api.main import app

    return app.openapi()


def test_spec_file_exists_at_the_documented_location() -> None:
    """`scripts/export_openapi.py` writes the repo root, and every doc link
    now points there. A spec at any other path is a mirror waiting to drift."""
    assert SPEC_PATH.is_file(), f"{SPEC_PATH} is missing. {_REGENERATE}"


def test_no_second_copy_of_the_spec_exists() -> None:
    """`docs/openapi.json` was a manually re-copied mirror and it drifted.
    One file, no copying step — this fails if the mirror comes back."""
    strays = [
        p
        for p in _REPO_ROOT.rglob("openapi.json")
        if p != SPEC_PATH and ".venv" not in p.parts and "node_modules" not in p.parts
    ]
    assert not strays, (
        "a second copy of the spec exists and will drift: "
        f"{[str(p.relative_to(_REPO_ROOT)) for p in strays]}. "
        "Link to ../openapi.json instead of copying it."
    )


def test_every_route_is_in_the_committed_spec() -> None:
    """The failure mode that actually bit: a new endpoint ships, the spec is
    not regenerated, and the frontend team reads its absence as 'unsupported'."""
    live = set(_live()["paths"])
    committed = set(_committed()["paths"])

    missing = sorted(live - committed)
    removed = sorted(committed - live)
    assert not missing, f"routes missing from openapi.json: {missing}. {_REGENERATE}"
    assert not removed, (
        f"openapi.json still documents routes the app no longer serves: {removed}. "
        f"{_REGENERATE}"
    )


def test_committed_spec_matches_the_live_app_exactly() -> None:
    """Not just the route list — request/response schemas, status codes and
    descriptions too. A frontend generating a client off a stale schema gets
    types that compile and then fail at runtime."""
    live = _live()
    committed = _committed()
    if live == committed:
        return

    differing = sorted(
        key for key in set(live) | set(committed) if live.get(key) != committed.get(key)
    )
    # Point at the specific paths rather than dumping two 180KB documents.
    detail = ""
    if "paths" in differing:
        live_paths, committed_paths = live["paths"], committed["paths"]
        changed = sorted(
            p
            for p in set(live_paths) | set(committed_paths)
            if live_paths.get(p) != committed_paths.get(p)
        )
        detail = f" Paths that differ: {changed}."

    raise AssertionError(
        f"openapi.json is out of date — top-level sections that differ: {differing}."
        f"{detail} {_REGENERATE}"
    )


def test_the_api_reference_states_the_real_path_and_operation_count() -> None:
    """`docs/02-api-reference.md` claims a path/operation count in its header.

    That number is the first thing a frontend engineer reads to decide
    whether the doc is trustworthy, and it had rotted twice — worse, the file
    ended up asserting two *different* stale counts in two places (a "33
    paths / 40 operations" header over a "35 paths / 42 operations"
    verification note), which is the self-contradicting-document failure this
    repo has been bitten by before. A number a human has to remember to bump
    is a number that goes stale; this makes forgetting fail the build.

    Counts the committed spec, not the live app: the two are already pinned
    to each other by the tests above, and reading the file keeps this guard
    meaningful even if the app fails to import for an unrelated reason.
    """
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    paths = len(spec["paths"])
    operations = sum(
        1
        for methods in spec["paths"].values()
        for verb in methods
        if verb.lower() in {"get", "post", "put", "patch", "delete", "head", "options"}
    )

    doc = (_REPO_ROOT / "docs" / "02-api-reference.md").read_text(encoding="utf-8")
    m = re.search(r"\*\*(\d+) paths / (\d+) operations\*\*", doc)
    assert m, (
        "docs/02-api-reference.md no longer states a '**N paths / M operations**' "
        "count in its header. If that sentence was removed deliberately, remove "
        "this guard too — do not leave it matching nothing."
    )
    stated = (int(m.group(1)), int(m.group(2)))
    assert stated == (paths, operations), (
        f"docs/02-api-reference.md says {stated[0]} paths / {stated[1]} operations, "
        f"but openapi.json has {paths} / {operations}. {_REGENERATE}"
    )
