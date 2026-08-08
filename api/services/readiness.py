"""Dependency probes behind `GET /ready`.

`/health` answers "is this process alive" and must stay trivial — it is what
tells a supervisor whether to restart the container, and it must never fail
because something *else* is down. `/ready` answers the different question of
whether this instance can usefully serve traffic, which means actually
touching Postgres, Redis, MinIO and the Celery fleet.

Not every dependency is equally fatal, and conflating them is how a readiness
probe takes down a system it was meant to protect:

  * **Postgres and Redis are required.** Every request reads the DB, and Redis
    carries both the job snapshots and the Celery broker. Without either, this
    instance genuinely cannot serve, so the probe fails with 503 and a load
    balancer should route away from it.
  * **MinIO and the worker queues are reported but not fatal.** With MinIO
    down, uploads and artifact downloads fail but everything else — listing
    projects, reading datasets, progress snapshots — still works. With no
    worker on a queue, submits still enqueue and
    `api/services/job_reconcile.py` now ends orphaned jobs properly rather
    than leaving them spinning. Returning 503 for either would pull the whole
    API out of rotation and take the UI down with it, which is strictly worse
    than serving in a degraded state that the response body names explicitly.

Worker health is reported **per queue** (`worker_gpu`, `worker_cpu`), not as
one aggregate `worker` check. A plain Celery `ping()` only proves *some* node
answered — on this deployment a CPU-only worker answers pings just fine while
the GPU worker is dead, so the old aggregate check reported `worker: ok`
straight through a GPU outage (proven live on the prod box). Calling
`inspect(...).active_queues()` instead names which queues each node actually
consumes, so a dead GPU worker shows up as `worker_gpu: unavailable` even
while `worker_cpu` stays healthy.

Every probe is bounded and failure-isolated: one slow dependency must not make
the readiness endpoint itself the outage.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from api.core.config import get_settings
from api.core.database import engine
from api.core.redis_client import get_redis_client

log = logging.getLogger("api.readiness")

# Long enough to survive a GC pause or a busy broker, short enough that a
# probe on a 5-10s interval never overlaps itself.
PROBE_TIMEOUT_SECONDS = 3.0

# The window `probe_worker_queues` gives Celery's `inspect` to collect replies.
# Half the outer bound, not `outer - 0.5`, and that difference was a live bug:
# `inspect` does NOT return as soon as replies arrive — it waits out its entire
# window, then adds broadcast and deserialisation cost. Measured on a
# two-worker box:
#
#     inner=0.5s -> 0.69s wall      inner=1.5s -> 2.09s wall
#     inner=1.0s -> 1.53s wall      inner=2.5s -> 3.22s wall
#
# so a 2.5s inner window reliably overshot `_guard`'s 3s `wait_for` and
# `/ready` reported `worker: unavailable` while both workers were answering
# perfectly. Overhead grows with node count, so the margin has to be
# proportional rather than a fixed subtraction. The window size does not
# affect whether workers are *found* — they reply in milliseconds; it only
# bounds how long we wait for stragglers.
WORKER_PROBE_TIMEOUT_SECONDS = PROBE_TIMEOUT_SECONDS / 2

# Dependencies this instance cannot serve a single request without.
REQUIRED = ("postgres", "redis")

# The queues a Celery worker can register for. `probe_worker_queues` reports
# one verdict per entry here (`worker_gpu`, `worker_cpu`) instead of a single
# aggregate `worker` check — see the module docstring for why an aggregate
# ping hides a dead GPU worker behind a healthy CPU one.
WORKER_QUEUES = ("gpu", "cpu")

OK = "ok"
UNAVAILABLE = "unavailable"


@dataclass
class ReadinessReport:
    checks: dict[str, str]

    @property
    def ready(self) -> bool:
        return all(self.checks.get(name) == OK for name in REQUIRED)

    @property
    def status(self) -> str:
        if not self.ready:
            return "unready"
        if any(v != OK for v in self.checks.values()):
            return "degraded"
        return "ok"

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "checks": dict(self.checks)}


async def _guard(name: str, probe) -> tuple[str, str]:
    """Run one probe, converting any failure or hang into `unavailable`.

    A probe that raises is a dependency that is down; a probe that hangs is
    also a dependency that is down, as far as a caller waiting on this
    endpoint is concerned. Neither may propagate.
    """
    try:
        await asyncio.wait_for(probe(), timeout=PROBE_TIMEOUT_SECONDS)
    except TimeoutError:
        log.warning("readiness: %s probe timed out after %ss", name, PROBE_TIMEOUT_SECONDS)
        return name, UNAVAILABLE
    except Exception:  # deliberately broad — any failure is "not ready", never a 500
        log.warning("readiness: %s probe failed", name, exc_info=True)
        return name, UNAVAILABLE
    return name, OK


async def _probe_postgres() -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _probe_redis() -> None:
    client = get_redis_client()
    try:
        await client.ping()
    finally:
        await client.aclose()


async def _probe_minio() -> None:
    # `workers/storage.py` owns the client factory. It imports only the minio
    # SDK and this config module — no Celery — so borrowing it here does not
    # drag the worker runtime into the API process. Deferred rather than
    # module-level to keep that dependency visible at the call site.
    from workers.storage import get_minio_client

    settings = get_settings()
    client = get_minio_client()
    # `bucket_exists` is a HEAD, so this is a real round-trip against a bucket
    # the app actually uses rather than a listing whose cost grows over time.
    await asyncio.to_thread(client.bucket_exists, settings.minio_datasets_bucket)


async def probe_worker_queues() -> dict[str, bool]:
    """Report, per queue in `WORKER_QUEUES`, whether some worker serves it.

    ONE `inspect(...).active_queues()` broadcast covers every queue — never
    one broadcast per queue, which would multiply the same overhead
    `WORKER_PROBE_TIMEOUT_SECONDS` already has to budget for. Public (no
    leading underscore) and side-effect-free on failure — it returns
    all-`False` rather than raising when nobody answers, so callers other
    than `/ready` (a metrics adapter reuses this) don't each need their own
    try/except around a broker hiccup.
    """
    from workers.celery_app import celery_app

    # See `WORKER_PROBE_TIMEOUT_SECONDS` for why this is proportional to the
    # outer bound rather than a fixed subtraction from it.
    inspector = celery_app.control.inspect(timeout=WORKER_PROBE_TIMEOUT_SECONDS)
    # Blocking broadcast — same reason `job_reconcile` offloads it.
    # `active_queues()` returns `{hostname: [queue_info, ...]}`, or `None`
    # when nobody answers (no workers, or a broker issue) — exactly the case
    # this probe exists to surface, so it becomes "every queue unserved"
    # rather than an exception.
    replies: dict[str, list[dict[str, Any]]] | None = await asyncio.to_thread(
        inspector.active_queues
    )
    served: set[str] = set()
    for queue_infos in (replies or {}).values():
        for queue_info in queue_infos or ():
            name = queue_info.get("name")
            if name:
                served.add(name)
    return {queue: queue in served for queue in WORKER_QUEUES}


async def _guard_worker_queues() -> dict[str, str]:
    """`_guard`'s isolation, applied to `probe_worker_queues` as a unit.

    A timeout or an unexpected exception here means we learned nothing about
    *any* queue, so every `worker_*` key is reported unavailable together —
    `probe_worker_queues` itself already turns "nobody answered" into
    all-`False` without raising, so this only catches the hang/error case.
    """
    try:
        served = await asyncio.wait_for(
            probe_worker_queues(), timeout=PROBE_TIMEOUT_SECONDS
        )
    except TimeoutError:
        log.warning(
            "readiness: worker queue probe timed out after %ss", PROBE_TIMEOUT_SECONDS
        )
        served = dict.fromkeys(WORKER_QUEUES, False)
    except Exception:  # deliberately broad — see `_guard`
        log.warning("readiness: worker queue probe failed", exc_info=True)
        served = dict.fromkeys(WORKER_QUEUES, False)
    return {f"worker_{queue}": (OK if ok else UNAVAILABLE) for queue, ok in served.items()}


_PROBES = {
    "postgres": _probe_postgres,
    "redis": _probe_redis,
    "minio": _probe_minio,
}


async def probe_dependencies() -> dict[str, bool]:
    """Report, per entry in `_PROBES`, whether that dependency answered OK.

    Looks `_PROBES` up on the module at call time (`readiness._PROBES`, via
    the module-level name below) rather than binding it in a default arg or a
    closure — tests monkeypatch `readiness._PROBES` to exercise fake
    dependencies, and a captured-at-import reference would silently keep
    probing the real ones. Public and side-effect-free on failure, same shape
    as `probe_worker_queues`: a metrics adapter (`slm_dependency_up`) reuses
    this directly rather than growing its own second gather over `_PROBES`,
    so the gauge and `/ready` can never disagree.
    """
    results = await asyncio.gather(*(_guard(name, probe) for name, probe in _PROBES.items()))
    return {name: status == OK for name, status in results}


async def check() -> ReadinessReport:
    """Probe every dependency concurrently and report per-dependency status.

    Concurrent, not sequential: run in series the endpoint's worst case is the
    sum of the timeouts, and a readiness probe that takes several seconds is
    indistinguishable from a hung one. The worker queues are probed as one
    unit alongside `_PROBES` — a single `_guard_worker_queues()` call, not one
    per queue — so the whole check still issues exactly one Celery broadcast.

    Consumes `probe_dependencies()` rather than gathering over `_PROBES`
    itself, so `/ready`'s verdict and `probe_dependencies()` (and anything
    built on it, like the `slm_dependency_up` gauge) share one code path and
    can never drift apart.
    """
    dep_results, worker_results = await asyncio.gather(
        probe_dependencies(),
        _guard_worker_queues(),
    )
    checks = {name: (OK if ok else UNAVAILABLE) for name, ok in dep_results.items()}
    checks.update(worker_results)
    return ReadinessReport(checks=checks)


__all__ = [
    "PROBE_TIMEOUT_SECONDS",
    "REQUIRED",
    "WORKER_QUEUES",
    "ReadinessReport",
    "check",
    "probe_dependencies",
    "probe_worker_queues",
]
