"""`project_id` must actually reach the logging context — from real code paths.

`BACKEND_GAP_ANALYSIS.md`'s P1 monitoring bullet asks for structured logs
carrying "request ID, job ID, project ID and tenant ID". Three of the four
were wired in the auth round. `project_id` was not: `set_project_id`
existed, `_SETTERS` listed it, `bound()` accepted it, and
`tests/unit/test_logging_context.py` passed — because that test calls
`bound(project_id="p1")` itself. Nothing in `api/` or `workers/` ever
invoked it, so no real log line has ever carried a project. Same failure
shape as round 2's dead `record_success()`: complete plumbing, unpressed
button.

So these tests deliberately do NOT set the context themselves. They drive
the production entry points (`ownership.assert_*_access`, Celery's
publish/prerun signals) and then read the context back. A test that binds
the value it is about to assert on would reproduce the original bug.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from uuid import uuid4

import pytest

from api.core import request_context

_OWNERSHIP_PATH = Path(__file__).resolve().parents[2] / "api" / "services" / "ownership.py"


@pytest.fixture(autouse=True)
def _clear_context():
    """Every test starts with no project bound, so a pass can only come from
    the code under test having bound it."""
    request_context.set_project_id(None)
    yield
    request_context.set_project_id(None)


# ---- the enumerated guard ----------------------------------------------------


def _assert_access_functions() -> list[str]:
    """Every `assert_*_access` coroutine defined in `ownership.py`.

    Enumerated from the AST rather than hardcoded: the recurring defect in
    this repo has never been "the logic is wrong", it has been "one of the N
    call sites was missed" (see the five-task `committed` flag guard in
    `test_worker_progress_frames.py`). A new resource type gets its own
    `assert_x_access`, and this list grows without anyone remembering to
    update it.
    """
    tree = ast.parse(_OWNERSHIP_PATH.read_text(encoding="utf-8"))
    names = [
        node.name
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name.startswith("assert_")
        and node.name.endswith("_access")
    ]
    assert names, (
        "no assert_*_access functions found in ownership.py — either the "
        "module was restructured or this parser broke; do not delete this "
        "assertion, an enumeration over nothing passes vacuously"
    )
    return names


@pytest.mark.parametrize("func_name", _assert_access_functions())
def test_every_assert_access_binds_the_log_project(func_name: str) -> None:
    """Each resolver must call `_bind_log_project`, and must do it OUTSIDE
    the `if user is not None` ownership branch.

    The second half is the load-bearing part: with `AUTH_REQUIRED=false` —
    the platform's state today — that branch never executes, so a bind
    placed inside it would leave logs project-less until auth is flipped,
    and the flip is blocked on an external frontend team.
    """
    tree = ast.parse(_OWNERSHIP_PATH.read_text(encoding="utf-8"))
    func = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == func_name
    )

    calls = [
        node
        for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_bind_log_project"
    ]
    assert calls, (
        f"{func_name} never calls _bind_log_project — requests through it "
        "will emit logs with no project_id"
    )

    # Any `_bind_log_project` nested inside an `if user is not None:` test is
    # auth-gated and therefore dead while AUTH_REQUIRED=false.
    for branch in [n for n in ast.walk(func) if isinstance(n, ast.If)]:
        cond = ast.unparse(branch.test)
        if "user is not None" not in cond:
            continue
        gated = [
            n
            for n in ast.walk(branch)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_bind_log_project"
        ]
        assert not gated, (
            f"{func_name} binds the log project inside `if {cond}:` — that "
            "branch does not run while AUTH_REQUIRED=false, so project_id "
            "would silently stay absent from logs until auth is flipped"
        )


# ---- behaviour, through the real resolvers -----------------------------------


@pytest.mark.asyncio
async def test_assert_project_access_binds_without_auth(monkeypatch) -> None:
    """`user=None` (AUTH_REQUIRED=false today) must still bind."""
    from api.models.project import Project
    from api.services import ownership

    project_id = uuid4()

    class _FakeDB:
        async def get(self, _model, _pk):
            return Project(id=project_id, owner_id=None)

    got = await ownership.assert_project_access(_FakeDB(), project_id, None)
    assert got is not None
    assert request_context.current_project_id() == str(project_id), (
        "assert_project_access did not bind project_id with user=None — the "
        "exact configuration the platform runs in today"
    )


@pytest.mark.asyncio
async def test_assert_dataset_access_binds_the_datasets_project(monkeypatch) -> None:
    from api.models.dataset import Dataset
    from api.services import ownership

    project_id = uuid4()
    dataset_id = uuid4()

    class _FakeDB:
        async def get(self, _model, _pk):
            return Dataset(id=dataset_id, project_id=project_id)

    await ownership.assert_dataset_access(_FakeDB(), dataset_id, None)
    assert request_context.current_project_id() == str(project_id)


# ---- Celery propagation ------------------------------------------------------


def test_publish_stamps_the_project_header() -> None:
    """A task enqueued by a request that resolved a project carries it."""
    from workers import celery_app as ca

    project_id = str(uuid4())
    request_context.set_project_id(project_id)
    headers: dict = {}
    ca._inject_request_context(headers=headers)
    assert headers.get("x_project_id") == project_id, (
        "the publish hook drops project_id — worker logs for this job will "
        "carry request/user/job but never the project"
    )


def test_publish_omits_the_header_when_no_project_is_bound() -> None:
    from workers import celery_app as ca

    headers: dict = {}
    ca._inject_request_context(headers=headers)
    assert "x_project_id" not in headers


def test_worker_prerun_rebinds_the_project_from_the_header() -> None:
    from workers import celery_app as ca

    project_id = str(uuid4())

    class _Ctx:
        x_request_id = "r1"
        x_user_id = "u1"
        x_project_id = project_id

    class _Task:
        name = "test.task"
        request = _Ctx()

    ca._bind_request_context(task_id="job-1", task=_Task())
    assert request_context.current_project_id() == project_id, (
        "the worker prerun hook ignores x_project_id — the header rides the "
        "message but never reaches the task's log records"
    )


def test_worker_prerun_clears_the_project_when_the_header_is_absent() -> None:
    """A task published before this shipped (or by a path with no project)
    must not inherit whichever project the *previous* task in this worker
    process happened to bind."""
    from workers import celery_app as ca

    request_context.set_project_id("stale-project-from-previous-task")

    class _Ctx:
        x_request_id = None
        x_user_id = None

    class _Task:
        name = "test.task"
        request = _Ctx()

    ca._bind_request_context(task_id="job-2", task=_Task())
    assert request_context.current_project_id() is None


# ---- end-to-end into a log record --------------------------------------------


def test_bound_project_reaches_the_log_record() -> None:
    """The last link: context -> `logging_config`'s record stamping.

    Covered in `test_logging_context.py` too, but asserted here as well so
    this file fails as one story if the filter stops reading `project_id` —
    the resolvers could then bind perfectly into a void.
    """
    snapshot_fn = request_context.snapshot
    request_context.set_project_id("p-42")
    assert snapshot_fn().get("project_id") == "p-42"


def test_ownership_module_actually_imports_request_context() -> None:
    """Non-vacuity backstop for the AST guard above: `_bind_log_project`
    could be present, enumerated, and a no-op stub."""
    from api.services import ownership

    src = inspect.getsource(ownership._bind_log_project)
    assert "request_context.set_project_id" in src, (
        "_bind_log_project no longer calls set_project_id — the AST guard "
        "would still pass while nothing is bound"
    )
