"""Structural (AST) contract: the training success paths actually invoke the
auto-pipeline hook — reviewer-added coverage for W2-T1's wiring.

`tests/unit/test_auto_pipeline_chain.py` proves what `enqueue_auto_pipeline`
does once called; nothing proved that `workers/tasks/training.py`'s
`train_manual` and `workers/tasks/hpo_training.py`'s `train_hpo` actually
call it, with the right kwargs, in the right place. This file closes that
gap using the same AST-walk technique as
`tests/unit/test_worker_zombie_cancel_contract.py` (a source-substring grep
cannot tell "hook present on the success path" from "hook mentioned in a
comment" — this file's assertions all operate on parsed call nodes).

Contract per task module:
  1. Exactly one `enqueue_auto_pipeline(...)` call site in the module.
  2. It passes `training_id=` and `artifact_id=` keyword arguments (the
     entrypoint's signature is keyword-only).
  3. It sits INSIDE a `try` whose handler catches `Exception` — a failure to
     enqueue the optional follow-on pipeline must never turn a successful
     training run into a reported failure.
  4. It sits AFTER the zombie-cancel early-return (`return {"status":
     "cancelled", ...}`) in source order — a run whose terminal-success
     write was discarded must never kick off an export/evaluate chain.
  5. It sits BEFORE the `JobCompleted` publish — the completion frame is the
     last act of the success path, and the hook belongs to that path.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from workers.tasks import hpo_training as hpo_task
from workers.tasks import training as training_task

_TASK_MODULES = {
    "training": training_task,
    "hpo_training": hpo_task,
}


def _module_tree(name: str) -> tuple[ast.Module, str]:
    source = Path(_TASK_MODULES[name].__file__).read_text(encoding="utf-8")
    return ast.parse(source), source


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _find_calls(tree: ast.AST, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node) == name
    ]


def _cancelled_return_lines(tree: ast.AST) -> list[int]:
    """Line numbers of `return {"status": "cancelled", ...}` statements."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)):
            continue
        for key, value in zip(node.value.keys, node.value.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "status"
                and isinstance(value, ast.Constant)
                and value.value == "cancelled"
            ):
                lines.append(node.lineno)
    return lines


def _job_completed_lines(tree: ast.AST) -> list[int]:
    return [c.lineno for c in _find_calls(tree, "JobCompleted")]


def _enclosing_try_catches_exception(tree: ast.Module, call: ast.Call) -> bool:
    """True if `call` is lexically inside a Try whose handlers include
    `except Exception` (bare or by name)."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        contains = any(call is inner for stmt in node.body for inner in ast.walk(stmt))
        if not contains:
            continue
        for handler in node.handlers:
            t = handler.type
            if t is None:
                return True
            if isinstance(t, ast.Name) and t.id in ("Exception", "BaseException"):
                return True
            if isinstance(t, ast.Tuple) and any(
                isinstance(e, ast.Name) and e.id in ("Exception", "BaseException")
                for e in t.elts
            ):
                return True
    return False


@pytest.mark.parametrize("task", sorted(_TASK_MODULES))
def test_success_path_calls_enqueue_auto_pipeline_exactly_once(task: str) -> None:
    tree, _ = _module_tree(task)
    calls = _find_calls(tree, "enqueue_auto_pipeline")
    assert len(calls) == 1, (
        f"{task}: expected exactly one enqueue_auto_pipeline call site, "
        f"found {len(calls)}"
    )


@pytest.mark.parametrize("task", sorted(_TASK_MODULES))
def test_hook_passes_training_id_and_artifact_id_kwargs(task: str) -> None:
    tree, _ = _module_tree(task)
    (call,) = _find_calls(tree, "enqueue_auto_pipeline")
    kwarg_names = {kw.arg for kw in call.keywords}
    assert {"training_id", "artifact_id"} <= kwarg_names, (
        f"{task}: enqueue_auto_pipeline must be called with training_id= and "
        f"artifact_id= keywords, got {kwarg_names}"
    )
    # `artifact_id` must be stringified — the entrypoint takes `str`, and the
    # local variable is a UUID.
    artifact_kw = next(kw for kw in call.keywords if kw.arg == "artifact_id")
    assert (
        isinstance(artifact_kw.value, ast.Call)
        and _call_name(artifact_kw.value) == "str"
    ), f"{task}: artifact_id kwarg should be str(artifact_id)"


@pytest.mark.parametrize("task", sorted(_TASK_MODULES))
def test_hook_is_wrapped_in_a_swallowing_try(task: str) -> None:
    tree, _ = _module_tree(task)
    (call,) = _find_calls(tree, "enqueue_auto_pipeline")
    assert _enclosing_try_catches_exception(tree, call), (
        f"{task}: enqueue_auto_pipeline must be wrapped in try/except "
        f"Exception — a failed enqueue must never mask a successful run"
    )


@pytest.mark.parametrize("task", sorted(_TASK_MODULES))
def test_hook_fires_after_zombie_cancel_return_and_before_job_completed(
    task: str,
) -> None:
    """Source-order contract: zombie-cancel early return (which the hook must
    be unreachable from) precedes the hook, and the JobCompleted publish
    follows it. Both anchors are required to exist so this can never pass
    vacuously.
    """
    tree, _ = _module_tree(task)
    (call,) = _find_calls(tree, "enqueue_auto_pipeline")
    cancelled_lines = _cancelled_return_lines(tree)
    completed_lines = _job_completed_lines(tree)

    assert cancelled_lines, f"{task}: zombie-cancel return anchor not found"
    assert completed_lines, f"{task}: JobCompleted anchor not found"

    assert max(cancelled_lines) < call.lineno, (
        f"{task}: the hook (line {call.lineno}) must come after the "
        f"zombie-cancel early return (last at line {max(cancelled_lines)}) — "
        f"a cancelled run must never enqueue the auto-pipeline"
    )
    assert call.lineno < max(completed_lines), (
        f"{task}: the hook (line {call.lineno}) must fire on the success "
        f"path, before the JobCompleted publish (line {max(completed_lines)})"
    )


def test_non_vacuity_unguarded_call_is_rejected() -> None:
    """Prove the try/except detector actually distinguishes guarded from
    unguarded calls rather than trivially returning True."""
    guarded = ast.parse(
        "try:\n"
        "    enqueue_auto_pipeline(training_id=t, artifact_id=str(a))\n"
        "except Exception:\n"
        "    pass\n"
    )
    unguarded = ast.parse("enqueue_auto_pipeline(training_id=t, artifact_id=str(a))\n")

    (guarded_call,) = _find_calls(guarded, "enqueue_auto_pipeline")
    (unguarded_call,) = _find_calls(unguarded, "enqueue_auto_pipeline")

    assert _enclosing_try_catches_exception(guarded, guarded_call)
    assert not _enclosing_try_catches_exception(unguarded, unguarded_call)
