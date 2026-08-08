"""The Celery worker must import without the API's auth stack.

The GPU worker image (docker/worker.Dockerfile) does not ship PyJWT — it has
no HTTP surface and no tokens to verify. `api/core/auth.py` does `import jwt`
at module scope, so any worker-reachable module that imports it (directly, or
via `api.services.ownership`) turns a type annotation into
`ModuleNotFoundError: No module named 'jwt'` at worker boot.

That is not hypothetical: adding `audit_service` to the five task bodies did
exactly this, and every unit test stayed green because **the dev environment
has PyJWT installed** — nothing about a normal `pytest` run can see this
class of bug; it only surfaced as a crash-looping `worker` container on real
infra (the vast.ai box). Round 2 added five more modules that workers import
transitively — `api.services.usage_service`, `api.services.model_pricing`,
`api.services.quota`, `api.services.circuit_breaker`, and
`ai_engine.data_gen.usage` — any one of which can reintroduce the same bug
via a stray `CurrentUser` type annotation or a module-scope `ownership`
import, so they're guarded here the same way `audit_service` is.

Each check runs in a subprocess with `jwt` blocked at the import-hook level,
which is the closest in-repo approximation of the worker image's site-packages.
The parametrized shape matches the house guards in
tests/unit/test_worker_progress_frames.py: adding a task module to the tuple
extends the check for free.

M1 ("metrics-core") added a second dependency the worker image must never
see: `prometheus_client`, imported by `api/core/metrics.py`. Same failure
mode (an API-only package reachable from a worker-boot module crash-loops
the GPU worker), same fix shape — a parallel import-hook block against the
same `_WORKER_BOOT_MODULES` tuple, further down this file.
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

# Round-2 modules the task bodies import transitively (usage/budget
# tracking, pricing lookups, quota checks, the circuit breaker around
# OpenRouter calls). Same risk as `audit_service`: any one of these can
# reach `api.core.auth`/PyJWT through a careless module-scope import or
# type annotation, and no ordinary test run would catch it.
_ROUND_2_SERVICE_MODULES = (
    "api.services.usage_service",
    "api.services.model_pricing",
    "api.services.quota",
    "api.services.circuit_breaker",
    "ai_engine.data_gen.usage",
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

# Same shape as the PyJWT hook above, for prometheus_client. api/core/metrics.py
# is an API-only module (pyproject.toml's `[metrics]` extra, installed in
# docker/api.Dockerfile but never docker/worker.Dockerfile) — a worker-boot
# module that imports it, directly or transitively, would crash-loop the GPU
# worker container the same way a stray `import jwt` did.
_BLOCK_PROMETHEUS_CLIENT_AND_IMPORT = """
import sys, importlib.abc


class _NoPrometheusClient(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name == "prometheus_client" or name.startswith("prometheus_client."):
            raise ModuleNotFoundError(
                "No module named 'prometheus_client' (simulating the worker image)"
            )
        return None


sys.meta_path.insert(0, _NoPrometheusClient())
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


def _import_without_prometheus_client(module: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _BLOCK_PROMETHEUS_CLIENT_AND_IMPORT.format(module=module)],
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


@pytest.mark.parametrize("module", _ROUND_2_SERVICE_MODULES)
def test_round_2_service_module_imports_without_pyjwt(module: str) -> None:
    """The five modules the round-2 task bodies import transitively
    (usage/budget tracking, pricing, quota, the OpenRouter circuit
    breaker) must each import cleanly in the worker's PyJWT-less
    environment."""
    result = _import_without_pyjwt(module)
    assert "IMPORTED" in result.stdout, (
        f"{module} cannot be imported in the worker image.\n"
        f"stderr:\n{result.stderr}"
    )


def test_usage_service_read_path_still_enforces_ownership() -> None:
    """Same guard as `test_the_read_path_still_enforces_ownership`, for
    `usage_service`'s own read path: deferring the `ownership` import into
    `list_project_usage` must not quietly drop the access check along with
    it — a deferred import nobody calls is worse than no deferral at all."""
    src = (_REPO_ROOT / "api" / "services" / "usage_service.py").read_text(
        encoding="utf-8"
    )
    body = src.split("async def list_project_usage")[1]
    assert "ownership.assert_project_access" in body
    assert "from api.services import ownership" in body, (
        "the deferred import must live inside list_project_usage, not at "
        "module scope"
    )


@pytest.mark.parametrize("module", _WORKER_BOOT_MODULES)
def test_worker_module_imports_without_prometheus_client(module: str) -> None:
    """api/core/metrics.py (M1) is an API-only module — prometheus_client
    lives in pyproject.toml's `[metrics]` extra, installed by
    docker/api.Dockerfile but deliberately not docker/worker.Dockerfile. No
    worker-boot module may import it, directly or transitively, or the GPU
    worker container crash-loops the same way it did for a stray `import
    jwt`."""
    result = _import_without_prometheus_client(module)
    assert "IMPORTED" in result.stdout, (
        f"{module} cannot be imported in the worker image "
        f"(prometheus_client missing).\nstderr:\n{result.stderr}"
    )
