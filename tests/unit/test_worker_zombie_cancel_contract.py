"""Contract tests for the zombie-cancel guards across all five worker tasks.

"Zombie-cancel" is the race where a job is cancelled (DB row flipped to
`JobStatus.CANCELLED` by the cancel endpoint) in one of two windows a worker
can land in:

  * before it ever flips the row to RUNNING (cancelled while still queued,
    or claimed by a worker after the cancel already landed) — the
    **start-check** guard;
  * after the row is CANCELLED but before the worker's own terminal-success
    write would otherwise clobber it back to COMPLETED (cancelled mid-run,
    the classic "zombie" window: the worker keeps working under a job the
    API has already declared dead) — the **completed-guard**.

Each of the five task modules (`data_generation`, `training`, `hpo_training`,
`model_export`, `evaluation`) now carries both guards plus a shared
`_cancelled_frame` helper that announces the terminal `JobFailed(..., error_type
="Cancelled")` frame on either path. This file is the cross-file contract that
keeps all five in lockstep — the recurring defect in this codebase has never
been "the guard logic is wrong", it has been "one of the five files was
missed" (see `tests/unit/test_worker_progress_frames.py`'s `_CANCELLABLE_TASKS`
docstring and `tests/unit/test_worker_audit_events.py`'s `_TERMINAL_TASKS`,
which this file mirrors the shape of).

Three layers:
  1. Membership — this file's module set must equal `_CANCELLABLE_TASKS`.
  2. Behavioral  — `_cancelled_frame(job_id)` actually builds the right frame.
  3. Structural (AST) — the RUNNING flip and the COMPLETED flip are each
     provably preceded, in their own enclosing function, by a reference to
     `JobStatus.CANCELLED`. A source-substring `assert "x" in source` test
     cannot tell "guarded" from "guard text present somewhere unrelated";
     the AST walk can, and the non-vacuity fixtures prove it actually
     rejects an unguarded function rather than trivially returning True.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from api.schemas.enums import WSMessageType
from api.schemas.progress import JobFailed
from workers.tasks import data_generation as sdg_task
from workers.tasks import evaluation as eval_task
from workers.tasks import hpo_training as hpo_task
from workers.tasks import model_export as export_task
from workers.tasks import training as training_task

# =============================================================================
# 1. Membership
# =============================================================================

_TASK_MODULES = {
    "data_generation": sdg_task,
    "training": training_task,
    "hpo_training": hpo_task,
    "model_export": export_task,
    "evaluation": eval_task,
}

_TASK_SOURCE = {
    name: Path(module.__file__).read_text(encoding="utf-8")
    for name, module in _TASK_MODULES.items()
}

# `model_export.py`'s terminal-status field is `export_status`
# (`ModelArtifact` has no generic `status` column — export completion is
# inferred from `gguf_uri`/`export_error_message` elsewhere, but the
# RUNNING/COMPLETED lifecycle field itself is `export_status`). The other
# four use plain `status`.
_STATUS_ATTR = {
    "data_generation": "status",
    "training": "status",
    "hpo_training": "status",
    "model_export": "export_status",
    "evaluation": "status",
}


def test_membership_matches_the_cancellable_task_set() -> None:
    """This file's module set must not drift from `_CANCELLABLE_TASKS` in
    `test_worker_progress_frames.py` — imported, not mirrored by hand, so
    adding a sixth cancellable task can't silently leave this file behind
    the way `hpo_training`/`evaluation` were left behind twice before.
    """
    from tests.unit.test_worker_progress_frames import _CANCELLABLE_TASKS

    assert set(_TASK_MODULES) == set(_CANCELLABLE_TASKS)


def test_status_attr_covers_every_task_module() -> None:
    assert set(_STATUS_ATTR) == set(_TASK_MODULES)


# =============================================================================
# 2. Behavioral — `_cancelled_frame` helper
# =============================================================================


@pytest.mark.parametrize("task", sorted(_TASK_MODULES))
def test_cancelled_frame_helper_exists(task: str) -> None:
    module = _TASK_MODULES[task]
    assert hasattr(module, "_cancelled_frame"), (
        f"{task}: missing the `_cancelled_frame` helper every zombie-cancel "
        f"exit path publishes"
    )
    assert callable(module._cancelled_frame)


@pytest.mark.parametrize("task", sorted(_TASK_MODULES))
def test_cancelled_frame_builds_the_right_job_failed(task: str) -> None:
    """Non-vacuous by construction: this actually calls the helper and
    inspects the object it returns, rather than grepping for its source."""
    module = _TASK_MODULES[task]
    f = module._cancelled_frame("j-123")

    assert isinstance(f, JobFailed)
    assert f.job_id == "j-123"
    assert f.type is WSMessageType.FAILED
    assert f.error == "job was cancelled"
    assert f.error_type == "Cancelled"


# =============================================================================
# 3. Structural (AST) — start-check + completed-guard
# =============================================================================


def _is_status_assign(node: ast.Assign, attr: str, status_value: str) -> bool:
    """True iff `node` is `<something>.<attr> = JobStatus.<status_value>`."""
    if len(node.targets) != 1:
        return False
    target = node.targets[0]
    if not isinstance(target, ast.Attribute) or target.attr != attr:
        return False
    value = node.value
    return (
        isinstance(value, ast.Attribute)
        and value.attr == status_value
        and isinstance(value.value, ast.Name)
        and value.value.id == "JobStatus"
    )


def _attribute_base_name(node: ast.AST) -> str | None:
    """For an `obj.attr` expression, return `"obj"` when `obj` is a plain
    `Name` (not e.g. a call result or a subscript) — `None` otherwise."""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return node.value.id
    return None


def _is_cancelled_expr(node: ast.AST) -> bool:
    """True iff `node` is exactly the expression `JobStatus.CANCELLED`."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "CANCELLED"
        and isinstance(node.value, ast.Name)
        and node.value.id == "JobStatus"
    )


def _cancelled_guard_object(node: ast.Compare) -> str | None:
    """If `node` is (one side of) an `==`/`!=` comparison against
    `JobStatus.CANCELLED`, and the other side is `<obj>.<attr>` for a plain
    name `obj`, return `obj`'s name. `None` for anything else (wrong
    operator, more than one comparator, `JobStatus.CANCELLED` compared
    against something that isn't a plain `obj.attr` access, ...).

    This is what makes the guard-detection precise rather than "some
    CANCELLED reference exists somewhere earlier in the function": every
    guard in these five files reads `<same-object-the-flip-targets>.<attr>
    == JobStatus.CANCELLED`, so keying on that object's name is what
    distinguishes "the guard actually wrapping THIS flip" from an unrelated
    CANCELLED check earlier in the same function (e.g. a start-check on a
    differently-named local guarding a DIFFERENT flip in that same
    function — see `data_generation.py`, where the start-check compares
    `ds.status` and the completed-guard compares `parent.status`; both are
    `JobStatus.CANCELLED` checks in the same enclosing function, but only
    one of them guards a given flip).
    """
    if len(node.ops) != 1 or not isinstance(node.ops[0], (ast.Eq, ast.NotEq)):
        return None
    left, right = node.left, node.comparators[0]
    for candidate, other in ((left, right), (right, left)):
        if _is_cancelled_expr(candidate):
            return _attribute_base_name(other)
    return None


class _FunctionScopeCollector(ast.NodeVisitor):
    """Buckets status-flip `Assign`s and `<obj>.<attr> ==/!= JobStatus.
    CANCELLED` guard comparisons by their NEAREST enclosing function AND by
    the object name involved.

    Function-scoping matters because `train_manual`'s RUNNING flip lives
    inside the small helper `_load_train_context`, guarded by a CANCELLED
    check in that *same* helper — but `train_manual` itself also contains
    plenty of unrelated CANCELLED references (in its own `except
    BaseException` handler) that must not leak in. Walking with a
    function-scope stack — pushing on `FunctionDef`/`AsyncFunctionDef`,
    popping on exit — means a nested `def` (e.g. `emit_progress`, `publish`)
    never leaks its statements into the outer task function's bucket, and
    vice versa.

    Object-name keying matters for the opposite reason: within ONE function,
    `data_generation.generate_synthetic_data` guards two different flips
    (`ds.status = RUNNING`, `parent.status = COMPLETED`) with two different
    checks (`ds.status == CANCELLED`, `parent.status == CANCELLED`). Without
    keying by object name, the earlier `ds` check would (wrongly) appear to
    guard the later `parent` flip too — exactly the false pass this file's
    mutation-testing matrix caught the naive version of this predicate on.
    """

    def __init__(self) -> None:
        self._stack: list[ast.AST] = []
        self.assigns: dict[int, list[ast.Assign]] = {}
        # func id -> object name -> [linenos of a CANCELLED guard on that object]
        self.cancelled_lines: dict[int, dict[str, list[int]]] = {}

    def _enter_function(self, node: ast.AST) -> None:
        self._stack.append(node)
        self.assigns.setdefault(id(node), [])
        self.cancelled_lines.setdefault(id(node), {})
        self.generic_visit(node)
        self._stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._enter_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._enter_function(node)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        if self._stack:
            self.assigns[id(self._stack[-1])].append(node)
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:  # noqa: N802
        if self._stack:
            obj_name = _cancelled_guard_object(node)
            if obj_name is not None:
                bucket = self.cancelled_lines[id(self._stack[-1])]
                bucket.setdefault(obj_name, []).append(node.lineno)
        self.generic_visit(node)


def _flip_is_guarded(source: str, attr: str, status_value: str) -> bool:
    """True iff EVERY `<obj>.<attr> = JobStatus.<status_value>` assignment
    found anywhere in `source` sits in a function that ALSO compares that
    same `<obj>.<something>` against `JobStatus.CANCELLED` at a strictly
    earlier line, in that same function.

    Returns False (rather than vacuously True) when no matching assignment
    exists at all — a source file that doesn't flip the status in question
    should never register as "guarded".
    """
    tree = ast.parse(source)
    collector = _FunctionScopeCollector()
    collector.visit(tree)

    matches: list[tuple[int, str, int]] = []  # (function id, obj name, assign lineno)
    for func_id, assigns in collector.assigns.items():
        for node in assigns:
            if not _is_status_assign(node, attr, status_value):
                continue
            obj_name = _attribute_base_name(node.targets[0])
            if obj_name is not None:
                matches.append((func_id, obj_name, node.lineno))

    if not matches:
        return False

    for func_id, obj_name, lineno in matches:
        guard_lines = collector.cancelled_lines.get(func_id, {}).get(obj_name, [])
        if not any(ln < lineno for ln in guard_lines):
            return False

    return True


def _running_flip_is_guarded(source: str, attr: str) -> bool:
    return _flip_is_guarded(source, attr, "RUNNING")


def _completed_flip_is_guarded(source: str, attr: str) -> bool:
    return _flip_is_guarded(source, attr, "COMPLETED")


# ---- non-vacuity fixtures ---------------------------------------------------
#
# Minimal snippets that flip the status with NO preceding CANCELLED
# reference anywhere in the enclosing function. If the predicate can't tell
# these apart from the guarded production code, it isn't testing anything.

_UNGUARDED_RUNNING_SNIPPET = """
from api.schemas.enums import JobStatus


def flip_running(obj):
    obj.status = JobStatus.RUNNING
"""

_UNGUARDED_COMPLETED_SNIPPET = """
from api.schemas.enums import JobStatus


def flip_completed(obj):
    obj.status = JobStatus.COMPLETED
"""

# A keyword arg in a constructor call must not be mistaken for a guarding
# CANCELLED reference OR for the flip itself: `status=JobStatus.COMPLETED`
# here is an `ast.keyword`, never an `ast.Assign`, so it must not satisfy
# `_is_status_assign`, and the unrelated `obj.status = JobStatus.COMPLETED`
# two lines later remains unguarded.
_UNGUARDED_COMPLETED_WITH_DECOY_KEYWORD_SNIPPET = """
from api.schemas.enums import JobStatus


def build_and_flip(obj, Row):
    row = Row(status=JobStatus.COMPLETED)
    obj.status = JobStatus.COMPLETED
    return row
"""


def test_start_check_predicate_rejects_unguarded_snippet() -> None:
    assert _running_flip_is_guarded(_UNGUARDED_RUNNING_SNIPPET, "status") is False


def test_completed_guard_predicate_rejects_unguarded_snippet() -> None:
    assert _completed_flip_is_guarded(_UNGUARDED_COMPLETED_SNIPPET, "status") is False


def test_completed_guard_predicate_ignores_keyword_arg_decoy() -> None:
    assert (
        _completed_flip_is_guarded(_UNGUARDED_COMPLETED_WITH_DECOY_KEYWORD_SNIPPET, "status")
        is False
    )


def test_flip_is_guarded_returns_false_when_flip_is_absent() -> None:
    """No matching assignment at all -> False, not a vacuous True."""
    assert _running_flip_is_guarded("x = 1\n", "status") is False
    assert _completed_flip_is_guarded("x = 1\n", "status") is False


# ---- the actual contract ----------------------------------------------------


@pytest.mark.parametrize("task", sorted(_TASK_MODULES))
def test_start_check_guards_the_running_flip(task: str) -> None:
    """Every RUNNING flip must sit behind an earlier-in-function check of
    `JobStatus.CANCELLED` — the "cancelled while still queued" guard."""
    attr = _STATUS_ATTR[task]
    assert _running_flip_is_guarded(_TASK_SOURCE[task], attr) is True, (
        f"{task}: found a `.{attr} = JobStatus.RUNNING` assignment whose "
        f"enclosing function has no earlier `JobStatus.CANCELLED` reference "
        f"— a job cancelled while still queued would get its status flipped "
        f"to RUNNING anyway"
    )


@pytest.mark.parametrize("task", sorted(_TASK_MODULES))
def test_completed_guard_guards_the_completed_flip(task: str) -> None:
    """Every COMPLETED flip must sit behind an earlier-in-function check of
    `JobStatus.CANCELLED` — the "cancelled mid-run" / zombie-window guard."""
    attr = _STATUS_ATTR[task]
    assert _completed_flip_is_guarded(_TASK_SOURCE[task], attr) is True, (
        f"{task}: found a `.{attr} = JobStatus.COMPLETED` assignment whose "
        f"enclosing function has no earlier `JobStatus.CANCELLED` reference "
        f"— a job cancelled mid-run could have its COMPLETED write clobber "
        f"the CANCELLED the cancel endpoint already set"
    )
