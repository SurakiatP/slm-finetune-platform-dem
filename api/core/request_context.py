"""Per-request context propagation via `contextvars`.

Holds the small set of identifiers ("request id", "user id", "project id",
"job id") that we want attached to every log line emitted while handling a
request or a Celery task, without threading them through every function
signature. Each is stored in its own `ContextVar` so it works correctly
across async tasks (FastAPI) as well as plain sync call stacks (Celery
workers), since `contextvars` is copied per-task/per-thread automatically.

`api/core/logging_config.py` reads these back out (via `snapshot()`) to
stamp them onto `logging.LogRecord`s. `api/core/exceptions.py` currently
mints its own ad-hoc correlation id (`uuid.uuid4().hex[:12]`) for unhandled
errors; that id is a plain short hex string, same shape as what belongs in
`request_id` here, so the two are compatible and can be unified later
without a format change.

**The task-boundary trap, and `_log_scope`** (found live on the pasaflow box
2026-08-09): Starlette's `BaseHTTPMiddleware` — which is what
`@app.middleware("http")` registers — runs `call_next` in a **separate
anyio task**. A child task gets a *copy* of the parent's context, so a
`ContextVar.set()` inside the endpoint (e.g. `ownership._bind_log_project`
setting `project_id`) is invisible to the middleware frame that emits the
access-log line: the log carried `request_id` (set by the middleware
itself, pre-split) but never `project_id`. The unit tests missed it
because they set and read in one context; production spans two.

The fix is `_log_scope`: a ContextVar holding a **mutable dict**, bound by
the middleware *before* the task split. The child task's context copy
points at the same dict object, so writes made by the setters below inside
the endpoint are visible to the middleware through the shared object even
though the contextvars themselves are not. `snapshot()` (and the getters)
fall back to the scope for anything the local context doesn't have. Where
no scope is bound — Celery workers, plain sync code — the setters skip the
write-through and everything behaves exactly as before.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_user_id: ContextVar[str | None] = ContextVar("user_id", default=None)
_project_id: ContextVar[str | None] = ContextVar("project_id", default=None)
_job_id: ContextVar[str | None] = ContextVar("job_id", default=None)

_ALL_VARS: dict[str, ContextVar[str | None]] = {
    "request_id": _request_id,
    "user_id": _user_id,
    "project_id": _project_id,
    "job_id": _job_id,
}

# Mutable per-request dict shared across the BaseHTTPMiddleware task split —
# see the module docstring. `None` (the default) means "no scope bound"
# (Celery workers, tests, sync code), in which case the setters skip the
# write-through entirely.
_log_scope: ContextVar[dict[str, str] | None] = ContextVar("log_scope", default=None)


def _scope_write(name: str, value: str | None) -> None:
    scope = _log_scope.get()
    if scope is None:
        return
    if value is None:
        scope.pop(name, None)
    else:
        scope[name] = value


# ---- getters ----------------------------------------------------------------
#
# Each getter prefers its own contextvar (always right within the task that
# set it) and falls back to the shared log scope, so a frame on the far side
# of the middleware task split — the access logger, the unhandled-exception
# handler — sees the same values the endpoint bound.


def _get(name: str, var: ContextVar[str | None]) -> str | None:
    value = var.get()
    if value is not None:
        return value
    scope = _log_scope.get()
    return scope.get(name) if scope is not None else None


def current_request_id() -> str | None:
    return _get("request_id", _request_id)


def current_user_id() -> str | None:
    return _get("user_id", _user_id)


def current_project_id() -> str | None:
    return _get("project_id", _project_id)


def current_job_id() -> str | None:
    return _get("job_id", _job_id)


# ---- setters (return the reset Token) ---------------------------------------


def set_request_id(v: str | None) -> Token:
    _scope_write("request_id", v)
    return _request_id.set(v)


def set_user_id(v: str | None) -> Token:
    _scope_write("user_id", v)
    return _user_id.set(v)


def set_project_id(v: str | None) -> Token:
    _scope_write("project_id", v)
    return _project_id.set(v)


def set_job_id(v: str | None) -> Token:
    _scope_write("job_id", v)
    return _job_id.set(v)


_SETTERS = {
    "request_id": set_request_id,
    "user_id": set_user_id,
    "project_id": set_project_id,
    "job_id": set_job_id,
}


# ---- scoped binding -----------------------------------------------------


@contextmanager
def bound(
    *,
    request_id: str | None = None,
    user_id: str | None = None,
    project_id: str | None = None,
    job_id: str | None = None,
) -> Iterator[None]:
    """Set the given context vars for the duration of the `with` block.

    Only vars passed explicitly (non-omitted keyword) are touched; each is
    reset to its prior value on exit, including when the block raises.

    Deliberately does NOT write through to the log scope: a scoped bind
    with a token-based reset cannot be mirrored into a plain dict without
    tracking prior dict state too. Its one production caller (the request
    middleware) binds *before* the task split, so the child inherits the
    contextvar copy and the scope is not needed for that direction.
    """

    kwargs = {
        "request_id": request_id,
        "user_id": user_id,
        "project_id": project_id,
        "job_id": job_id,
    }
    tokens: list[tuple[ContextVar[str | None], Token]] = []
    try:
        for name, value in kwargs.items():
            if value is None:
                continue
            var = _ALL_VARS[name]
            tokens.append((var, var.set(value)))
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


# ---- the shared per-request log scope ---------------------------------------


@contextmanager
def request_log_scope() -> Iterator[None]:
    """Bind a fresh shared dict for the duration of one request.

    Must be entered by the outermost HTTP middleware BEFORE `call_next`
    (i.e. before `BaseHTTPMiddleware` splits off the endpoint's task), so
    the endpoint's context copy and the middleware frame share the same
    dict object. Each request gets its own dict — concurrent requests
    cannot see each other's writes, because the ContextVar itself is still
    copied per task tree as usual; only the dict *within one request* is
    shared.
    """

    token = _log_scope.set({})
    try:
        yield
    finally:
        _log_scope.reset(token)


# ---- introspection / teardown ------------------------------------------


def snapshot() -> dict[str, str]:
    """Return currently-set context vars, omitting any that are unset.

    Merges the shared log scope underneath the local contextvars: a value
    set on the far side of the middleware task split (endpoint) is only
    reachable from this side (access logger) through the scope dict. The
    local contextvar wins whenever both are set — within one request they
    can only disagree transiently, and the local value is the caller's own.
    """

    merged: dict[str, str] = dict(_log_scope.get() or {})
    merged.update(
        {name: value for name, var in _ALL_VARS.items() if (value := var.get()) is not None}
    )
    return merged


def clear() -> None:
    """Reset all four context vars to unset (None), and empty the shared
    log scope if one is bound — a cleared context must not resurrect
    values through the scope fallback."""

    for var in _ALL_VARS.values():
        var.set(None)
    scope = _log_scope.get()
    if scope is not None:
        scope.clear()
