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


# ---- getters ----------------------------------------------------------------


def current_request_id() -> str | None:
    return _request_id.get()


def current_user_id() -> str | None:
    return _user_id.get()


def current_project_id() -> str | None:
    return _project_id.get()


def current_job_id() -> str | None:
    return _job_id.get()


# ---- setters (return the reset Token) ---------------------------------------


def set_request_id(v: str | None) -> Token:
    return _request_id.set(v)


def set_user_id(v: str | None) -> Token:
    return _user_id.set(v)


def set_project_id(v: str | None) -> Token:
    return _project_id.set(v)


def set_job_id(v: str | None) -> Token:
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


# ---- introspection / teardown ------------------------------------------


def snapshot() -> dict[str, str]:
    """Return currently-set context vars, omitting any that are unset."""

    return {name: value for name, var in _ALL_VARS.items() if (value := var.get()) is not None}


def clear() -> None:
    """Reset all four context vars to unset (None)."""

    for var in _ALL_VARS.values():
        var.set(None)
