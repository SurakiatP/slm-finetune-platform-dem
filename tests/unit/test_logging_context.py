"""Tests for api/core/request_context.py and api/core/logging_config.py."""

from __future__ import annotations

import ast
import io
import json
import logging
import sys
import sysconfig

import pytest

from api.core import logging_config, request_context
from api.core.logging_config import JsonFormatter, RequestContextFilter, configure_logging
from api.core.request_context import bound, clear, snapshot


@pytest.fixture(autouse=True)
def _clear_context():
    """Make sure no test leaks context vars into the next one."""

    clear()
    yield
    clear()


def _make_captured_logger(name: str) -> tuple[logging.Logger, io.StringIO]:
    """A standalone logger writing JSON lines to an in-memory buffer."""

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestContextFilter())

    logger = logging.getLogger(name)
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger, stream


def _lines(stream: io.StringIO) -> list[dict]:
    text = stream.getvalue().strip()
    if not text:
        return []
    return [json.loads(line) for line in text.splitlines()]


# ---- request_context.bound() -------------------------------------------


def test_bound_context_appears_in_log_record():
    logger, stream = _make_captured_logger("test.logging.bound")

    with bound(request_id="abc", user_id="u1"):
        logger.info("inside context")

    records = _lines(stream)
    assert len(records) == 1
    assert records[0]["request_id"] == "abc"
    assert records[0]["user_id"] == "u1"
    assert records[0]["msg"] == "inside context"


def test_bound_resets_on_exception():
    assert snapshot() == {}
    with pytest.raises(ValueError):
        with bound(request_id="abc"):
            assert request_context.current_request_id() == "abc"
            raise ValueError("boom")
    assert snapshot() == {}


# ---- absent (not null) outside context ----------------------------------


def test_context_keys_absent_outside_bound_block():
    logger, stream = _make_captured_logger("test.logging.outside")

    logger.info("outside any context")

    records = _lines(stream)
    assert len(records) == 1
    record = records[0]
    assert "request_id" not in record
    assert "user_id" not in record
    assert "project_id" not in record
    assert "job_id" not in record


def test_snapshot_omits_unset_vars():
    assert snapshot() == {}
    with bound(project_id="p1"):
        assert snapshot() == {"project_id": "p1"}
    assert snapshot() == {}


# ---- configure_logging idempotency --------------------------------------


def test_configure_logging_is_idempotent():
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    try:
        configure_logging("INFO")
        first_count = sum(
            1 for h in root.handlers if getattr(h, logging_config._HANDLER_MARKER, False)
        )
        configure_logging("DEBUG")
        second_count = sum(
            1 for h in root.handlers if getattr(h, logging_config._HANDLER_MARKER, False)
        )

        assert first_count == 1
        assert second_count == 1
        assert root.level == logging.DEBUG
    finally:
        # Restore the root logger so we don't leak a handler into other tests.
        root.handlers = original_handlers


# ---- exc_info -> "exc" key -----------------------------------------------


def test_exc_info_produces_exc_key():
    logger, stream = _make_captured_logger("test.logging.exc")

    try:
        raise RuntimeError("kaboom")
    except RuntimeError:
        logger.exception("something failed")

    records = _lines(stream)
    assert len(records) == 1
    assert "exc" in records[0]
    assert "RuntimeError" in records[0]["exc"]
    assert "kaboom" in records[0]["exc"]


# ---- extra kwargs survive -------------------------------------------------


def test_extra_kwargs_are_merged_into_output():
    logger, stream = _make_captured_logger("test.logging.extra")

    logger.info("with extra", extra={"correlation_id": "corr-123"})

    records = _lines(stream)
    assert records[0]["correlation_id"] == "corr-123"


def test_non_serializable_extra_does_not_blow_up():
    logger, stream = _make_captured_logger("test.logging.nonserializable")

    class Weird:
        def __repr__(self) -> str:
            return "<Weird>"

    logger.info("with weird extra", extra={"thing": Weird()})

    records = _lines(stream)
    assert records[0]["thing"] == "<Weird>"


# ---- every emitted line is valid JSON ------------------------------------


def test_every_line_survives_json_loads():
    logger, stream = _make_captured_logger("test.logging.jsonlines")

    with bound(request_id="r1", user_id="u1", project_id="p1", job_id="j1"):
        logger.warning("line one")
    logger.error("line two")

    lines = stream.getvalue().strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        parsed = json.loads(line)
        assert "ts" in parsed
        assert "level" in parsed
        assert "logger" in parsed


# ---- stdlib-only source assertion ----------------------------------------


def test_logging_config_imports_nothing_outside_stdlib():
    source_path = logging_config.__file__
    with open(source_path, "r", encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source, filename=source_path)

    stdlib_names = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else set(
        sysconfig.get_paths()
    )

    top_level_imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level_imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                # relative import — inherently local to this project
                continue
            if node.module:
                top_level_imports.add(node.module.split(".")[0])

    assert top_level_imports, "expected at least one import to check"

    for name in top_level_imports:
        is_stdlib = name in stdlib_names
        is_local_project_code = name == "api"
        assert is_stdlib or is_local_project_code, (
            f"logging_config.py imports third-party module {name!r}; "
            "it must only use the stdlib (plus this project's own api.* code)"
        )
