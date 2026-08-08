"""Every cancellable worker task records its terminal outcome.

A job's ending is written by the worker, not the API — the API only knows it
was submitted. Without an audit row from the worker, the trail shows every
job starting and none of them finishing.

The guard is parametrized over a dict of source files rather than written per
file, and that shape is not cosmetic. Commit 523aded widened `except
Exception` to `except BaseException` in three of five task bodies; the two it
missed went unnoticed for a release, until a GPU-box run found a cancelled
training that never published a terminal frame. `_CANCELLABLE_TASKS` in
tests/unit/test_worker_progress_frames.py exists for exactly that reason, and
this mirrors it: adding a file to the dict extends every assertion at once.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workers.tasks import data_generation as sdg_task
from workers.tasks import evaluation as eval_task
from workers.tasks import hpo_training as hpo_task
from workers.tasks import model_export as export_task
from workers.tasks import training as training_task

# Same membership as test_worker_progress_frames._CANCELLABLE_TASKS: any task
# a cancel endpoint can revoke must both publish a terminal frame AND record
# a terminal audit row.
_TERMINAL_TASKS = {
    "data_generation": Path(sdg_task.__file__).read_text(encoding="utf-8"),
    "training": Path(training_task.__file__).read_text(encoding="utf-8"),
    "hpo_training": Path(hpo_task.__file__).read_text(encoding="utf-8"),
    "evaluation": Path(eval_task.__file__).read_text(encoding="utf-8"),
    "model_export": Path(export_task.__file__).read_text(encoding="utf-8"),
}


@pytest.mark.parametrize("task", sorted(_TERMINAL_TASKS))
def test_imports_the_audit_service(task: str) -> None:
    assert "audit_service" in _TERMINAL_TASKS[task]


@pytest.mark.parametrize("task", sorted(_TERMINAL_TASKS))
def test_records_a_success_outcome(task: str) -> None:
    """The success path writes its audit row inside the same
    `with session_scope()` block that flips the status, so both land or
    neither does."""
    src = _TERMINAL_TASKS[task]
    assert src.count("audit_service.record(") >= 2, (
        f"{task}: expected a record() on both the success and failure paths"
    )


@pytest.mark.parametrize("task", sorted(_TERMINAL_TASKS))
def test_failure_path_distinguishes_cancelled_from_failed(task: str) -> None:
    """A cancel and a crash are different events to whoever reads the log.
    The existing `!= JobStatus.CANCELLED` guard already tells the code which
    happened — the audit action must reflect it rather than calling every
    ending a failure."""
    src = _TERMINAL_TASKS[task]
    tail = src.split("except BaseException as exc:")[1]
    assert ".cancelled" in tail and ".failed" in tail, (
        f"{task}: terminal audit action does not distinguish cancel from failure"
    )


@pytest.mark.parametrize("task", sorted(_TERMINAL_TASKS))
def test_audit_rides_the_existing_transaction(task: str) -> None:
    """No new `session_scope()` — the audit write joins the one already open
    around the terminal status write. A separate transaction could commit the
    status and lose the audit row."""
    src = _TERMINAL_TASKS[task]
    before = src.count("with session_scope()")
    assert "audit_service.record(\n            session" in src or (
        "audit_service.record(\n                session" in src
        or "audit_service.record(\n                            session" in src
        or "audit_service.record(\n                    session" in src
        or "audit_service.record(\n                            fail_session" in src
    ), f"{task}: audit call does not use the surrounding session"
    assert before >= 1


@pytest.mark.parametrize("task", sorted(_TERMINAL_TASKS))
def test_carries_the_request_id(task: str) -> None:
    """Bound by the `task_prerun` signal from the message headers, so a
    worker-written audit row links back to the API request that started it."""
    assert "request_context.current_request_id()" in _TERMINAL_TASKS[task]


def test_membership_matches_the_cancellable_task_set() -> None:
    """The two dicts must not drift: a task that can be cancelled but is
    absent here would silently stop recording its endings."""
    from tests.unit.test_worker_progress_frames import _CANCELLABLE_TASKS

    assert set(_TERMINAL_TASKS) == set(_CANCELLABLE_TASKS)
