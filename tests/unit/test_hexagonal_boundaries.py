"""Hexagonal-boundary guard for `ai_engine/`.

`ai_engine/` is pure domain logic — CLAUDE.md's "No FastAPI/Celery imports
here, ever" convention, and half of the same architectural line that
`test_worker_import_surface.py` guards from the other side (that file
proves the worker doesn't transitively pull in `api.core.auth`'s PyJWT
dependency; this file proves `ai_engine/` itself never reaches *up* into
`fastapi`, `celery`, `sqlalchemy`, or `api.core.config` — the framework,
task-queue, and settings layers that only make sense above the domain
layer). Keeping this one-way (`api/` and `workers/` depend on `ai_engine/`,
never the reverse) is what makes `ai_engine/` importable, and testable, in
a process that has none of those packages installed — the exact property
the worker-image bug already showed the cost of losing.

This is a source-level (AST) guard, not an import-time one: it walks every
`.py` file under `ai_engine/` and inspects its `import`/`from ... import`
statements directly (anywhere in the file, not just module scope, so a
deferred import inside a function is caught too), rather than relying on
some test happening to import the module in a stripped-down environment.

Two things verified while writing this guard, so it fails only on a real
*regression* rather than failing on day one:

  * `ai_engine/data_gen/generator.py` (and several siblings —
    `data_formatters.py`, `validators.py`, `callbacks.py`, `prompts.py`,
    `deduplicator.py`, `semantic_guard.py`, `holdout_split.py`, the `hpo/`
    objective and search-space modules, ...) import from `api.schemas.*`
    (`TaskType`, `ToolDefinition`, `SDGRequestWithSeed`, ...). `api.schemas`
    is plain Pydantic models with no FastAPI/Celery/SQLAlchemy machinery
    behind it and is a distinct dotted path from `api.core.config` — it is
    not one of the four banned imports below, so it needs no exception
    entry. It's called out here only so a future reader doesn't mistake
    the `api.core.config` ban for a blanket "no `api.*` import" rule.
  * `ai_engine/training/mlflow_logger.py` imports `get_settings` from
    `api.core.config` directly, at module scope — that *is* one of the
    four banned imports. It predates this task, editing it is out of this
    task's scope (owned by another agent working the same round), and it
    is left as-is here. Rather than silently exempting it, it's recorded
    as a named, single-file exception in `_KNOWN_EXCEPTIONS` below, with
    its own accuracy check (`test_known_exceptions_are_still_accurate`)
    so it can't quietly go stale and hide a real fix, and so anyone
    reading this file sees the violation instead of a passing test.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AI_ENGINE_ROOT = _REPO_ROOT / "ai_engine"

# The four layers `ai_engine/` must never reach into. Matched as an exact
# module or any submodule of it (`sqlalchemy.orm` counts as `sqlalchemy`,
# `api.core.config.foo` counts as `api.core.config`, etc).
_BANNED_MODULES = ("fastapi", "celery", "sqlalchemy", "api.core.config")

# (path relative to ai_engine/, banned module) pairs that are known,
# pre-existing violations of the rule above and are intentionally not
# failed on — see the module docstring. Anything not listed here is a real
# regression and must fail the test.
_KNOWN_EXCEPTIONS: set[tuple[str, str]] = {
    ("training/mlflow_logger.py", "api.core.config"),
}


def _ai_engine_files() -> list[Path]:
    return sorted(_AI_ENGINE_ROOT.rglob("*.py"))


def _is_banned(module: str | None) -> str | None:
    """Return which entry of `_BANNED_MODULES` `module` matches (as itself
    or a submodule of it), or None if it matches none of them."""
    if module is None:
        return None
    for banned in _BANNED_MODULES:
        if module == banned or module.startswith(banned + "."):
            return banned
    return None


def _banned_imports(path: Path) -> set[str]:
    """Every entry of `_BANNED_MODULES` imported anywhere in `path`, via
    `import x`, `import x.y`, or `from x import y`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                banned = _is_banned(alias.name)
                if banned:
                    found.add(banned)
        elif isinstance(node, ast.ImportFrom):
            banned = _is_banned(node.module)
            if banned:
                found.add(banned)
    return found


@pytest.mark.parametrize(
    "path",
    _ai_engine_files(),
    ids=[str(p.relative_to(_AI_ENGINE_ROOT)) for p in _ai_engine_files()],
)
def test_ai_engine_module_does_not_cross_hexagonal_boundary(path: Path) -> None:
    """Walk the real files rather than hardcoding a list, so a brand-new
    `ai_engine` module is covered automatically the moment it's added."""
    rel = str(path.relative_to(_AI_ENGINE_ROOT))
    banned_found = _banned_imports(path)
    unexpected = {b for b in banned_found if (rel, b) not in _KNOWN_EXCEPTIONS}
    assert not unexpected, (
        f"ai_engine/{rel} imports {sorted(unexpected)}, crossing the "
        f"hexagonal boundary — ai_engine/ must never import fastapi, "
        f"celery, sqlalchemy, or api.core.config. See the module docstring "
        f"of tests/unit/test_hexagonal_boundaries.py."
    )


def test_known_exceptions_are_still_accurate() -> None:
    """A stale exception entry (banned import removed but the entry left
    behind) would hide the day this guard could safely be tightened, so
    each entry in `_KNOWN_EXCEPTIONS` must still be a real, present
    violation."""
    for rel, banned in _KNOWN_EXCEPTIONS:
        path = _AI_ENGINE_ROOT / rel
        assert path.is_file(), f"known-exception path no longer exists: ai_engine/{rel}"
        assert banned in _banned_imports(path), (
            f"ai_engine/{rel} no longer imports {banned} — remove this "
            f"stale entry from _KNOWN_EXCEPTIONS in "
            f"tests/unit/test_hexagonal_boundaries.py"
        )
