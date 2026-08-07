"""CPU/GPU Celery queue split.

Before this, `docker-compose.yml` ran a single `worker` service with no
`task_routes` at all, so all five task types (sdg.generate, train.manual,
train.hpo, model.export, evaluation.run) serialized into one
`--concurrency=1` slot. A long CPU-only SDG run (OpenRouter calls, no GPU
involved) would block GPU training for the whole platform.

Fix: `worker` now only drains `-Q gpu`, a new `worker-cpu` service drains
`-Q cpu`, and `workers/celery_app.py` routes `sdg.*` to the `cpu` queue
with everything else falling through to `task_default_queue="gpu"`.

Like `test_compose_port_exposure.py`, the compose-file half of this is a
text/YAML guard rather than an integration test — the file IS the
deployment topology, and there's no runtime behaviour to observe short of
actually starting containers.

The routing half is asserted by task **name** (not module path) on
purpose: `task_routes` keys glob against
`@celery_app.task(name=...)`, and a route keyed on a module path (e.g.
`workers.tasks.data_generation.*`) would silently match nothing — that
trap already cost this project once (see the comment in
workers/celery_app.py). Naming the exact task strings here is what would
catch a regression back to that mistake.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from workers.celery_app import celery_app

_COMPOSE_PATH = Path(__file__).resolve().parents[2] / "docker-compose.yml"
_COMPOSE_TEXT = _COMPOSE_PATH.read_text(encoding="utf-8")
_COMPOSE = yaml.safe_load(_COMPOSE_TEXT)


def test_compose_file_is_valid_yaml() -> None:
    assert _COMPOSE is not None
    assert "services" in _COMPOSE


def _command_str(service: str) -> str:
    """Commands are YAML lists of tokens; join them so `-Q gpu` style
    substring checks work regardless of how the list is split."""
    command = _COMPOSE["services"][service]["command"]
    if isinstance(command, list):
        return " ".join(str(c) for c in command)
    return str(command)


def test_worker_command_uses_gpu_queue() -> None:
    command = _command_str("worker")
    assert "-Q gpu" in command, (
        f"worker command {command!r} does not restrict itself to the gpu queue "
        "— it would fall back to Celery's default queue and could pick up "
        "sdg.* tasks meant for worker-cpu"
    )


def test_worker_keeps_concurrency_one_and_gpu_deploy() -> None:
    service = _COMPOSE["services"]["worker"]
    assert "--concurrency=1" in _command_str("worker")
    assert "deploy" in service, "worker lost its GPU deploy.reservations block"


def test_worker_cpu_service_exists_and_is_configured() -> None:
    assert "worker-cpu" in _COMPOSE["services"], "worker-cpu service is missing"
    service = _COMPOSE["services"]["worker-cpu"]

    command = _command_str("worker-cpu")
    assert "-Q cpu" in command, f"worker-cpu command {command!r} does not target the cpu queue"
    assert "--concurrency=2" in command, f"worker-cpu command {command!r} does not set concurrency=2"

    assert service.get("image") == "ghcr.io/surakiatp/slm-api:dev", (
        "worker-cpu should reuse the API image, not build its own"
    )
    assert "build" not in service, "worker-cpu must not have its own build: block"


def test_worker_cpu_has_no_ports() -> None:
    """Mirrors test_compose_port_exposure.py's guard: only `api` may publish
    a port. A `worker-cpu` with a `ports:` block would break that guard and
    widen the attack surface for no reason — it serves nothing."""
    assert "ports" not in _COMPOSE["services"]["worker-cpu"]


def test_worker_cpu_has_no_gpu_deploy_block() -> None:
    """worker-cpu is CPU-only by design; a GPU reservation here would just
    make it fail to schedule on a single-GPU host that doesn't need it."""
    assert "deploy" not in _COMPOSE["services"]["worker-cpu"]


def test_worker_cpu_placed_after_worker() -> None:
    """Load-bearing ordering: test_compose_port_exposure.py slices this file
    on the literal `"  worker:"` marker to isolate the `api` service block.
    `"  worker-cpu:"` does not contain that substring, so worker-cpu must
    come after worker in the file for that slice to stay correct."""
    worker_idx = _COMPOSE_TEXT.index("  worker:")
    worker_cpu_idx = _COMPOSE_TEXT.index("  worker-cpu:")
    assert worker_cpu_idx > worker_idx


# ---- Routing, asserted by task NAME -----------------------------------
#
# `celery_app.amqp.router.route(...)` is the real dispatch path Celery uses
# when a task is sent — going through it (rather than reimplementing the
# glob match here) means this test breaks if task_routes stops being wired
# up the way celery_app.py expects, not just if the dict literal changes.


def _resolved_queue(task_name: str) -> str:
    routing = celery_app.amqp.router.route({}, task_name)
    return routing["queue"].name if hasattr(routing["queue"], "name") else routing["queue"]


@pytest.mark.parametrize("task_name", ["sdg.generate"])
def test_sdg_routes_to_cpu_queue(task_name: str) -> None:
    assert _resolved_queue(task_name) == "cpu"


@pytest.mark.parametrize(
    "task_name",
    ["train.manual", "train.hpo", "model.export", "evaluation.run"],
)
def test_gpu_bound_tasks_route_to_gpu_queue(task_name: str) -> None:
    assert _resolved_queue(task_name) == "gpu"


def test_task_default_queue_is_gpu() -> None:
    """Every task that isn't explicitly routed falls through to the GPU
    queue. This is the safer default: an unrouted task landing on `worker`
    (gpu) just wastes a GPU slot briefly; landing on `worker-cpu` for a task
    that actually needs CUDA would fail outright."""
    assert celery_app.conf.task_default_queue == "gpu"


def test_sdg_route_is_keyed_by_task_name_not_module_path() -> None:
    """Guards against reintroducing the exact trap this file's module
    docstring describes: a route keyed on the module path glob would not
    appear in task_routes as a name-shaped pattern, or would fail to match
    the real task name via the router."""
    routes = celery_app.conf.task_routes
    # `task_routes` as configured in celery_app.py is a flat
    # {pattern: {"queue": ...}} dict; iterate defensively in case a future
    # edit turns it into Celery's other accepted shape, a list of such dicts.
    patterns = [
        pattern
        for route_map in (routes if isinstance(routes, list) else [routes])
        for pattern in route_map
    ]
    assert any(p.startswith("sdg.") for p in patterns), (
        "expected an 'sdg.*'-shaped (name-keyed) route in task_routes, "
        f"got patterns={patterns!r}"
    )
    assert not any(p.startswith("workers.tasks.") for p in patterns), (
        "a route keyed on a module path (e.g. 'workers.tasks.data_generation.*') "
        "silently matches nothing — task_routes globs against the task NAME "
        "passed to @celery_app.task(name=...), not the module it's defined in"
    )
