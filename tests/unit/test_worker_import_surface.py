"""The Celery worker must import without the API's auth stack.

The GPU worker image (docker/worker.Dockerfile) does not ship PyJWT — it has
no HTTP surface and no tokens to verify. `api/core/auth.py` does `import jwt`
at module scope, so any worker-reachable module that imports it (directly, or
via `api.services.ownership`) turns a type annotation into
`ModuleNotFoundError: No module named 'jwt'` at worker boot.

That is not hypothetical: adding `audit_service` to the five task bodies did
exactly this, and every unit test stayed green because the dev environment
has PyJWT installed. The failure only appeared as a crash-looping `worker`
container on the vast.ai box.

Each check runs in a subprocess with `jwt` blocked at the import-hook level,
which is the closest in-repo approximation of the worker image's site-packages.
The parametrized shape matches the house guards in
tests/unit/test_worker_progress_frames.py: adding a task module to the tuple
extends the check for free.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Every module Celery imports at worker boot. `workers.celery_app` is the
# entrypoint (`celery -A workers.celery_app worker`); the task modules are
# pulled in by its `include`/autodiscovery.
_WORKER_BOOT_MODULES = (
    "workers.celery_app",
    "workers.tasks.data_generation",
    "workers.tasks.training",
    "workers.tasks.hpo_training",
    "workers.tasks.evaluation",
    "workers.tasks.model_export",
)

_BLOCK_JWT_AND_IMPORT = """
import sys, importlib.abc


class _NoPyJWT(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name == "jwt" or name.startswith("jwt."):
            raise ModuleNotFoundError(
                "No module named 'jwt' (simulating the worker image)"
            )
        return None


sys.meta_path.insert(0, _NoPyJWT())
import {module}
print("IMPORTED")
"""


def _import_without_pyjwt(module: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _BLOCK_JWT_AND_IMPORT.format(module=module)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        env={
            **_child_env(),
            "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test_unused",
        },
    )


def _child_env() -> dict[str, str]:
    import os

    return dict(os.environ)


@pytest.mark.parametrize("module", _WORKER_BOOT_MODULES)
def test_worker_module_imports_without_pyjwt(module: str) -> None:
    result = _import_without_pyjwt(module)
    assert "IMPORTED" in result.stdout, (
        f"{module} cannot be imported in the worker image.\n"
        f"stderr:\n{result.stderr}"
    )


def test_audit_service_is_importable_without_pyjwt() -> None:
    """The specific module that regressed: workers call `record()`, which
    needs neither a token nor an ownership check."""
    result = _import_without_pyjwt("api.services.audit_service")
    assert "IMPORTED" in result.stdout, result.stderr


def test_the_read_path_still_enforces_ownership() -> None:
    """Deferring the `ownership` import must not quietly drop the access
    check — `list_activity`'s first act is still `assert_project_access`."""
    src = (_REPO_ROOT / "api" / "services" / "audit_service.py").read_text(
        encoding="utf-8"
    )
    body = src.split("async def list_activity")[1]
    assert "ownership.assert_project_access" in body
    assert "from api.services import ownership" in body, (
        "the deferred import must live inside list_activity, not at module scope"
    )
