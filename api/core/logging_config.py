"""Structured JSON logging, stdlib only.

Emits one JSON object per line on the root logger so log aggregation
(anything reading stdout — journald, docker logs, a log shipper) doesn't
need to parse a custom text format. Every record automatically carries the
current request context (`request_id`, `user_id`, `project_id`, `job_id`)
via `RequestContextFilter`, sourced from `api.core.request_context`.

`api/main.py` currently calls `logging.basicConfig(format="%(asctime)s
[%(levelname)s] %(name)s: %(message)s")`, and `workers/celery_app.py` has
an equivalent call in a `@setup_logging.connect` handler. Both are meant to
be replaced by a call to `configure_logging(level)` — the signature here is
deliberately just `(level: str) -> None` so either call site can swap in
`configure_logging(settings.log_level)` (or a Celery-supplied level) with
no further plumbing.

No third-party imports live in this module — keep it that way.
"""

from __future__ import annotations

import datetime
import json
import logging

from api.core.request_context import snapshot

_HANDLER_MARKER = "_slm_json_handler"

# Attributes that `logging.LogRecord` sets on every record. Anything else
# found on `record.__dict__` came from `extra={...}` (or our own filter)
# and gets merged into the JSON output verbatim.
_STANDARD_RECORD_ATTRS = frozenset(
    logging.LogRecord(
        name="",
        level=0,
        pathname="",
        lineno=0,
        msg="",
        args=(),
        exc_info=None,
    ).__dict__.keys()
) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Renders each `LogRecord` as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.datetime.fromtimestamp(
                record.created, tz=datetime.timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_ATTRS:
                continue
            payload[key] = value

        return json.dumps(payload, default=str)


class RequestContextFilter(logging.Filter):
    """Stamps the current request context onto every record.

    Keys are only set when present in `snapshot()` — an unset context var
    must be absent from the record (and thus the emitted JSON), never
    present with a `null` value.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in snapshot().items():
            setattr(record, key, value)
        return True


def configure_logging(level: str) -> None:
    """Install the JSON handler on the root logger (idempotent).

    Safe to call more than once (e.g. once from `api/main.py` startup and
    once from a Celery `setup_logging` hook in the same process during
    tests): a second call removes the handler installed by the first
    before adding a new one, so handlers never stack.
    """

    root = logging.getLogger()

    for existing in list(root.handlers):
        if getattr(existing, _HANDLER_MARKER, False):
            root.removeHandler(existing)

    handler = logging.StreamHandler()
    setattr(handler, _HANDLER_MARKER, True)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestContextFilter())

    root.addHandler(handler)
    root.setLevel(level)

    for logger_name in ("uvicorn.access", "uvicorn.error", "celery"):
        logger = logging.getLogger(logger_name)
        if not any(getattr(f, _HANDLER_MARKER, False) for f in logger.filters):
            context_filter = RequestContextFilter()
            setattr(context_filter, _HANDLER_MARKER, True)
            logger.addFilter(context_filter)
