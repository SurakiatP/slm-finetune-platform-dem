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
  * **MinIO and the worker are reported but not fatal.** With MinIO down,
    uploads and artifact downloads fail but everything else — listing
    projects, reading datasets, progress snapshots — still works. With no
    worker, submits still enqueue and `api/services/job_reconcile.py` now ends
    orphaned jobs properly rather than leaving them spinning. Returning 503
    for either would pull the whole API out of rotation and take the UI down
    with it, which is strictly worse than serving in a degraded state that the
    response body names explicitly.

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

# Dependencies this instance cannot serve a single request without.
REQUIRED = ("postgres", "redis")

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


async def _probe_worker() -> None:
    from workers.celery_app import celery_app

    inspector = celery_app.control.inspect(timeout=PROBE_TIMEOUT_SECONDS - 0.5)
    # Blocking broadcast — same reason `job_reconcile` offloads it. `ping()`
    # returns None when nobody answers, which is exactly the case this probe
    # exists to surface, so it is turned into a failure explicitly.
    replies = await asyncio.to_thread(inspector.ping)
    if not replies:
        raise RuntimeError("no celery worker answered ping")


_PROBES = {
    "postgres": _probe_postgres,
    "redis": _probe_redis,
    "minio": _probe_minio,
    "worker": _probe_worker,
}


async def check() -> ReadinessReport:
    """Probe every dependency concurrently and report per-dependency status.

    Concurrent, not sequential: run in series the endpoint's worst case is the
    sum of four timeouts, and a readiness probe that takes 12 seconds is
    indistinguishable from a hung one.
    """
    results = await asyncio.gather(
        *(_guard(name, probe) for name, probe in _PROBES.items())
    )
    return ReadinessReport(checks=dict(results))


__all__ = ["PROBE_TIMEOUT_SECONDS", "REQUIRED", "ReadinessReport", "check"]
