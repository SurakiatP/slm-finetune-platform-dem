"""One request id, from the click to the worker log line hours later.

A job starts in the API and finishes in a worker minutes or hours later. If
the two processes do not share an identifier, "why did this training fail"
means correlating two log streams by timestamp and hoping. These tests pin
the chain end to end:

    client  ->  X-Request-ID response header
            ->  every API log line for that request
            ->  the 500 correlation id the user is told to quote
            ->  the Celery message headers
            ->  every worker log line for that job

Nothing here needs a broker: the Celery signal handlers are invoked directly,
which is also the only way to test them deterministically.
"""

from __future__ import annotations

import json
import logging
from io import StringIO

import pytest

from api.core import request_context
from api.core.logging_config import JsonFormatter, RequestContextFilter


@pytest.fixture
def captured():
    """Capture root log output the way production emits it — as JSON lines."""
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestContextFilter())
    root = logging.getLogger()
    root.addHandler(handler)
    previous = root.level
    root.setLevel(logging.INFO)
    yield stream
    root.removeHandler(handler)
    root.setLevel(previous)
    request_context.clear()


def _lines(stream: StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


# =============================================================================
# 1. The API half
# =============================================================================


class TestApiSide:
    def test_every_line_in_a_bound_scope_carries_the_request_id(self, captured) -> None:
        with request_context.bound(request_id="req-abc123", user_id="user-1"):
            logging.getLogger("api.test").info("first")
            logging.getLogger("api.other").info("second")

        lines = _lines(captured)
        assert len(lines) == 2
        assert {line["request_id"] for line in lines} == {"req-abc123"}
        assert {line["user_id"] for line in lines} == {"user-1"}

    def test_outside_a_scope_the_keys_are_absent_not_null(self, captured) -> None:
        """Absent, not None: a null request_id in a log aggregator is a value
        you can accidentally group by."""
        logging.getLogger("api.test").info("unbound")
        line = _lines(captured)[0]
        assert "request_id" not in line
        assert "user_id" not in line

    def test_context_does_not_leak_past_its_scope(self, captured) -> None:
        with request_context.bound(request_id="req-inner"):
            logging.getLogger("api.test").info("inside")
        logging.getLogger("api.test").info("outside")

        inside, outside = _lines(captured)
        assert inside["request_id"] == "req-inner"
        assert "request_id" not in outside


# =============================================================================
# 2. The API -> worker handoff
# =============================================================================


class TestCeleryPropagation:
    def test_publish_stamps_the_context_onto_the_message_headers(self) -> None:
        """Headers, not kwargs — kwargs are part of each task's signature, and
        five task bodies should not change shape for logging's sake."""
        from workers.celery_app import _inject_request_context

        headers: dict = {}
        with request_context.bound(request_id="req-xyz789", user_id="user-7"):
            _inject_request_context(headers=headers)

        assert headers["x_request_id"] == "req-xyz789"
        assert headers["x_user_id"] == "user-7"

    def test_publish_outside_a_request_stamps_nothing(self) -> None:
        """A task enqueued by a beat schedule or a shell has no request."""
        from workers.celery_app import _inject_request_context

        headers: dict = {}
        request_context.clear()
        _inject_request_context(headers=headers)
        assert headers == {}

    def test_worker_rebinds_and_logs_inherit_it(self, captured) -> None:
        """THE end-to-end assertion (acceptance criterion 8): a line logged in
        the worker carries the same request_id the API minted."""
        from workers.celery_app import _bind_request_context, _clear_request_context

        class _Request:
            x_request_id = "req-xyz789"
            x_user_id = "user-7"

        class _Task:
            name = "workers.tasks.training.run_training"
            request = _Request()

        _bind_request_context(task_id="celery-task-42", task=_Task())
        logging.getLogger("workers.tasks.training").info("epoch 1 done")
        _clear_request_context(task_id="celery-task-42", task=_Task(), state="SUCCESS")

        lines = _lines(captured)
        worker_lines = [line for line in lines if line.get("request_id") == "req-xyz789"]
        assert worker_lines, "worker log lines lost the API's request id"
        assert any(line["msg"] == "epoch 1 done" for line in worker_lines)
        # job_id is bound from the Celery task id — the same string the client
        # subscribes to on /ws/jobs/{job_id}, so a user report maps to a grep.
        assert all(line["job_id"] == "celery-task-42" for line in worker_lines)

    def test_context_is_cleared_between_tasks(self, captured) -> None:
        """`worker_max_tasks_per_child=1` makes this unlikely to matter, but a
        leaked id would silently attribute one job's logs to another."""
        from workers.celery_app import _bind_request_context, _clear_request_context

        class _Task:
            name = "t"
            request = type("R", (), {"x_request_id": "req-first"})()

        _bind_request_context(task_id="task-1", task=_Task())
        _clear_request_context(task_id="task-1", task=_Task(), state="SUCCESS")
        logging.getLogger("workers.tasks.other").info("after")

        assert "request_id" not in _lines(captured)[-1]


# =============================================================================
# 3. Source guards
# =============================================================================


def test_basicconfig_is_gone_from_both_entrypoints() -> None:
    """Two competing handler installs means duplicated or unformatted lines."""
    import pathlib

    import api.main
    import workers.celery_app

    for module in (api.main, workers.celery_app):
        src = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        assert "logging.basicConfig(" not in src, f"{module.__name__} still calls basicConfig"
        assert "configure_logging(" in src


def test_the_500_correlation_id_is_the_request_id() -> None:
    """The client already has this value from the X-Request-ID header, so the
    id it is told to quote back must be the same one — not a second uuid."""
    import pathlib

    import api.core.exceptions

    src = pathlib.Path(api.core.exceptions.__file__).read_text(encoding="utf-8")
    assert "request_context.current_request_id()" in src
    assert 'getattr(request.state, "request_id", None)' in src
