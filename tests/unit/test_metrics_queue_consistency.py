"""Queue-set consistency guard for M9 "cardinality-guard-and-suite-reconciliation".

The gpu/cpu queue split (ADR-010) is spelled out independently in FOUR
places, and nothing before this file asserted they all agree:

  1. `api/services/readiness.py`'s `WORKER_QUEUES` — the queues
     `probe_worker_queues()` reports one `worker_gpu`/`worker_cpu` verdict
     for on `/ready`.
  2. `api/services/metrics_sources.py`'s `QUEUES` — the queues
     `queue_depths()` runs `LLEN` against for `slm_queue_depth`.
  3. `workers/celery_app.py`'s actual Celery routing config —
     `task_default_queue` plus whatever queues appear in `task_routes`'
     values — the ground truth for which queue a task really lands on.
  4. `docker-compose.yml`'s worker service `command:` blocks — the `-Q`
     flag each `celery worker` process is actually started with, i.e. which
     queues anything is listening on at all.

A drift between any two of these is a silent outage mode: e.g. if (1)/(2)
say `("gpu", "cpu")` but a compose edit narrows a worker's `-Q` flag to just
`gpu`, `cpu`-queued tasks (sdg.generate) would enqueue successfully and sit
forever with nothing consuming them, while `/ready` and `/metrics` both keep
reporting a healthy queue right up until the backlog is noticed some other
way.

House convention: enumerated over parsed/introspected structure (real
Celery `conf` object, real parsed YAML), not regexed/substring-matched
against source text — see test_mlflow_provisioning.py's module docstring —
with an explicit non-vacuity assertion ahead of every enumeration this file
relies on.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from api.services import metrics_sources, readiness
from workers.celery_app import celery_app

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE_PATH = _REPO_ROOT / "docker-compose.yml"
_COMPOSE = yaml.safe_load(_COMPOSE_PATH.read_text(encoding="utf-8"))

# Named once so every assertion message below can point at all four
# locations that must stay in sync, not just the two values being directly
# compared in that particular check.
_LOCATIONS = (
    "api.services.readiness.WORKER_QUEUES, "
    "api.services.metrics_sources.QUEUES, "
    "workers.celery_app's celery_app.conf (task_default_queue + task_routes), "
    "and docker-compose.yml's worker service `-Q` command flags"
)


def _celery_queue_set() -> set[str]:
    """The queue set Celery routing itself resolves to: `task_default_queue`
    plus every queue named in a `task_routes` value.

    Reads `celery_app.conf` programmatically (never regexes
    workers/celery_app.py's source) so this tracks the config Celery
    actually loaded, not a string that merely looks like it. Defensive about
    `task_routes` shape (a flat dict, or Celery's other accepted shape, a
    list of such dicts) the same way test_queue_routing.py's
    `test_sdg_route_is_keyed_by_task_name_not_module_path` already is.
    """
    conf = celery_app.conf
    queues = {conf.task_default_queue}
    routes = conf.task_routes
    route_maps = routes if isinstance(routes, list) else [routes]
    for route_map in route_maps:
        for route in route_map.values():
            queue = route["queue"]
            queues.add(queue.name if hasattr(queue, "name") else queue)
    return queues


def _compose_worker_queue_map() -> dict[str, set[str]]:
    """{service_name: {queue names}} for every compose service whose
    `command:` includes a `-Q` flag.

    Commands are YAML lists of tokens (`["celery", "-A", ..., "-Q", "gpu",
    ...]`); this walks that list rather than substring-matching the joined
    command, so a queue name that happens to also appear as a substring of
    an unrelated token (an image tag, a flag value) can't be mistaken for
    the `-Q` argument.
    """
    services = _COMPOSE["services"]
    result: dict[str, set[str]] = {}
    for name, service in services.items():
        command = service.get("command")
        if command is None:
            continue
        tokens = [str(t) for t in command] if isinstance(command, list) else str(command).split()
        if "-Q" not in tokens:
            continue
        idx = tokens.index("-Q")
        assert idx + 1 < len(tokens), f"docker-compose.yml service {name!r} has a trailing -Q with no queue argument"
        queue_arg = tokens[idx + 1]
        result[name] = {q.strip() for q in queue_arg.split(",") if q.strip()}
    return result


# ---- 1. readiness.WORKER_QUEUES == metrics_sources.QUEUES (as sets) --------


def test_readiness_and_metrics_sources_queue_sets_agree() -> None:
    readiness_queues = set(readiness.WORKER_QUEUES)
    metrics_sources_queues = set(metrics_sources.QUEUES)
    assert readiness_queues == metrics_sources_queues, (
        f"readiness.WORKER_QUEUES={sorted(readiness_queues)} disagrees with "
        f"metrics_sources.QUEUES={sorted(metrics_sources_queues)} — these "
        f"must stay identical along with the other two queue-split sources "
        f"({_LOCATIONS})"
    )


# ---- 2. that set == {task_default_queue} ∪ {queues named in task_routes} --


def test_readiness_queue_set_matches_actual_celery_routing_config() -> None:
    readiness_queues = set(readiness.WORKER_QUEUES)
    celery_queues = _celery_queue_set()
    # Non-vacuity backstop: a Celery conf that resolved to an empty queue
    # set would make the equality check below meaningless.
    assert celery_queues, (
        "workers.celery_app's celery_app.conf resolved to an empty queue "
        f"set (task_default_queue + task_routes) — cannot cross-check "
        f"against {_LOCATIONS}"
    )
    assert readiness_queues == celery_queues, (
        f"readiness.WORKER_QUEUES={sorted(readiness_queues)} disagrees with "
        f"the queue set Celery routing actually resolves to "
        f"{sorted(celery_queues)} (task_default_queue="
        f"{celery_app.conf.task_default_queue!r}, task_routes="
        f"{celery_app.conf.task_routes!r}) — all four queue-split sources "
        f"must agree: {_LOCATIONS}"
    )


# ---- 3. that set == the union of every compose worker service's -Q flags --


def test_compose_worker_service_queues_match_the_same_set() -> None:
    worker_queue_map = _compose_worker_queue_map()
    # Non-vacuity backstop: if no service in docker-compose.yml declared a
    # `-Q` flag at all (a bad edit, or a renamed flag), the union below would
    # be empty and the equality check meaningless.
    assert worker_queue_map, (
        "no service in docker-compose.yml has a `-Q` flag in its `command:` — "
        f"expected at least the `worker` and `worker-cpu` services; cannot "
        f"cross-check against {_LOCATIONS}"
    )

    compose_queues: set[str] = set()
    for queues in worker_queue_map.values():
        compose_queues |= queues

    readiness_queues = set(readiness.WORKER_QUEUES)
    assert readiness_queues == compose_queues, (
        f"readiness.WORKER_QUEUES={sorted(readiness_queues)} disagrees with "
        f"the union of docker-compose.yml worker service `-Q` flags "
        f"{sorted(compose_queues)} (services found: {worker_queue_map!r}) — "
        f"all four queue-split sources must agree: {_LOCATIONS}"
    )
