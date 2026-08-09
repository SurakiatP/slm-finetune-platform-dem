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

Round two of the same lesson (pasaflow box, 2026-08-09): the tests above
were still one context short. `BaseHTTPMiddleware` runs `call_next` in a
separate anyio task, so a contextvar bound inside the endpoint never
reaches the middleware frame that emits the access-log line — every
assertion here passed while the deployed access log carried no
`project_id`. The "task boundary" section below therefore asserts across
a real task split and into a real emitted `LogRecord`, through the real
`_request_context_middleware`, because that is exactly the surface the
first version of this file did not cover.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import logging
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


# ---- the task boundary -------------------------------------------------------
#
# `asyncio.create_task` copies the parent context at creation, exactly like
# the anyio task `BaseHTTPMiddleware` spawns for `call_next` — so these
# tests reproduce the production topology (bind in child, read in parent)
# without standing up Starlette internals.


@pytest.mark.asyncio
async def test_bind_inside_a_child_task_reaches_the_parent_snapshot() -> None:
    """The mechanism the deployed access log proved missing: with a log
    scope bound (as the middleware does before `call_next`), a
    `set_project_id` inside the child task must be visible to the parent
    frame's `snapshot()` and getters."""
    project_id = str(uuid4())

    async def endpoint() -> None:
        request_context.set_project_id(project_id)

    with request_context.request_log_scope():
        await asyncio.create_task(endpoint())
        assert request_context.snapshot().get("project_id") == project_id, (
            "a project bound inside the endpoint's task did not reach the "
            "parent snapshot — the access-log line is project-less again"
        )
        assert request_context.current_project_id() == project_id


@pytest.mark.asyncio
async def test_without_a_scope_the_task_boundary_swallows_the_bind() -> None:
    """Non-vacuity backstop for the test above: contextvars alone do NOT
    cross the task boundary. If this ever fails, task context semantics
    changed and the scope test proves nothing."""

    async def endpoint() -> None:
        request_context.set_project_id("p-child-only")

    await asyncio.create_task(endpoint())
    assert request_context.snapshot().get("project_id") is None


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _middleware_test_app():
    """A fresh FastAPI app running the REAL `_request_context_middleware`.

    Deliberately not `api.main.app` itself: dispatching real requests
    through the full app in-process previously bound the global async DB
    engine to a throwaway event loop and broke unrelated tests in the
    container run (see `42e5cf6`). These endpoints touch no DB; the
    middleware function is the production object, so removing
    `request_log_scope()` from `api/main.py` fails this test.
    """
    from fastapi import FastAPI

    from api import main as api_main

    app = FastAPI()

    @app.get("/with-project")
    async def with_project() -> dict:
        # Both setters that production calls inside the endpoint task:
        # `ownership._bind_log_project` and `auth`'s `set_user_id`
        # (api/core/auth.py) — user_id had the exact same boundary bug.
        request_context.set_project_id("p-e2e")
        request_context.set_user_id("u-e2e")
        return {"ok": True}

    @app.get("/without-project")
    async def without_project() -> dict:
        return {"ok": True}

    app.middleware("http")(api_main._request_context_middleware)
    return app


async def _drive(app, paths: list[str]) -> list[logging.LogRecord]:
    """Request each path and return the access-log records, one per path."""
    import httpx

    from api.core.logging_config import RequestContextFilter

    handler = _CaptureHandler()
    handler.addFilter(RequestContextFilter())
    api_logger = logging.getLogger("api")
    old_level = api_logger.level
    api_logger.addHandler(handler)
    api_logger.setLevel(logging.INFO)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            for path in paths:
                response = await client.get(path)
                assert response.status_code == 200
    finally:
        api_logger.removeHandler(handler)
        api_logger.setLevel(old_level)

    records = []
    for path in paths:
        matches = [r for r in handler.records if getattr(r, "path", None) == path]
        assert len(matches) == 1, (
            f"expected exactly one access-log record for {path}, got "
            f"{len(matches)} — the capture is broken, not the middleware"
        )
        records.append(matches[0])
    return records


@pytest.mark.asyncio
async def test_project_id_reaches_the_real_access_log_record() -> None:
    """The assertion the deployed box falsified on 2026-08-09: the
    middleware's own `log.info` line — emitted on the parent side of the
    task split — must carry the project the endpoint bound."""
    (record,) = await _drive(_middleware_test_app(), ["/with-project"])
    assert getattr(record, "project_id", None) == "p-e2e", (
        "the access-log record has no project_id — the middleware is not "
        "opening a request_log_scope around call_next"
    )
    assert getattr(record, "request_id", None), (
        "request_id vanished from the access log — the fix regressed the "
        "part that already worked"
    )
    assert getattr(record, "user_id", None) == "u-e2e", (
        "the access-log record has no user_id — auth's set_user_id "
        "(api/core/auth.py) crosses the same task boundary and was silently "
        "lost the same way project_id was"
    )


@pytest.mark.asyncio
async def test_a_projectless_request_does_not_inherit_the_previous_ones() -> None:
    """Each request gets a fresh scope dict: a request that resolves no
    project must not carry the previous request's — the failure mode of a
    module-global dict instead of a per-request one."""
    first, second = await _drive(
        _middleware_test_app(), ["/with-project", "/without-project"]
    )
    assert getattr(first, "project_id", None) == "p-e2e"
    assert not hasattr(second, "project_id"), (
        "a projectless request logged the previous request's project_id — "
        "the log scope is being shared across requests"
    )


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
