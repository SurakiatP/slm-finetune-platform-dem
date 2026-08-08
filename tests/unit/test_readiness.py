"""`/ready` reports every dependency but only fails on the ones that matter.

BACKEND_GAP_ANALYSIS.md P1: "เพิ่ม `/ready` สำหรับตรวจ PostgreSQL, Redis, MinIO
และ worker; คง `/health` ไว้เป็น liveness check".

The interesting decision is not *which* dependencies get probed — the doc lists
them — but which ones make the probe fail. A readiness probe that 503s because
the Celery worker is down pulls the API out of its load balancer and takes the
whole UI offline, including every read that was working fine. That is a worse
outage than the one it reports. So Postgres and Redis are fatal (no request can
be served without them) and MinIO/worker degrade instead.

Worker health is further split **per queue** (`worker_gpu`, `worker_cpu`)
rather than one aggregate `worker` check — see `TestPerQueueWorkerProbing`
for the bug that split was built to fix: a CPU-only worker answers a plain
`ping()` just fine while the GPU worker is dead, so the old aggregate check
reported `worker: ok` straight through a GPU outage (proven live on the prod
box).

These tests exist mostly to stop that distinction being "simplified" later.
No real Postgres, Redis, MinIO or broker — every probe is monkeypatched.
"""

from __future__ import annotations

import asyncio

import pytest

from api.services import readiness


def _fake_probes(monkeypatch, **outcomes) -> None:
    """Replace the postgres/redis/minio probe table with ones that succeed or
    raise on command.

    `outcomes` maps a dependency name to True (healthy) or False (down). This
    no longer covers the worker — that is probed separately via
    `probe_worker_queues`; see `_fake_celery_inspect`.
    """

    def make(ok: bool):
        async def probe() -> None:
            if not ok:
                raise ConnectionError("simulated outage")

        return probe

    monkeypatch.setattr(
        readiness, "_PROBES", {name: make(ok) for name, ok in outcomes.items()}
    )


ALL_UP = {"postgres": True, "redis": True, "minio": True}


class _FakeInspector:
    """Stands in for `celery_app.control.inspect(...)`, recording how many
    times `active_queues()` is broadcast so tests can assert on it."""

    def __init__(self, replies) -> None:
        self._replies = replies
        self.active_queues_calls = 0

    def active_queues(self):
        self.active_queues_calls += 1
        return self._replies


def _fake_celery_inspect(monkeypatch, replies) -> _FakeInspector:
    """Patch `workers.celery_app.celery_app` so `probe_worker_queues` talks to
    a fake broadcast instead of a real Celery fleet.

    `replies` is whatever `inspect(...).active_queues()` should return:
    `{hostname: [{"name": queue_name, ...}, ...]}`, or `None` for "nobody
    answered". Returns the `_FakeInspector` so a test can assert on
    `active_queues_calls`.

    `probe_worker_queues` does `from workers.celery_app import celery_app`
    *inside* the function body, so it re-reads this module attribute on every
    call — patching it here is enough, no need to reach into `readiness`.
    """
    import workers.celery_app as celery_app_module

    inspector = _FakeInspector(replies)

    class _FakeControl:
        def inspect(self, timeout=None):
            return inspector

    class _FakeCeleryApp:
        control = _FakeControl()

    monkeypatch.setattr(celery_app_module, "celery_app", _FakeCeleryApp())
    return inspector


@pytest.fixture(autouse=True)
def _worker_queues_healthy_by_default(monkeypatch):
    """Every test that isn't specifically about worker-queue behaviour gets a
    healthy two-queue worker fleet, so it never touches a real Celery broker.
    Tests that care about worker status call `_fake_celery_inspect` again to
    override this — a later `monkeypatch.setattr` in the same test wins."""
    _fake_celery_inspect(
        monkeypatch, {"default-worker@host": [{"name": "gpu"}, {"name": "cpu"}]}
    )
    yield


# =============================================================================
# 1. Which failures are fatal
# =============================================================================


class TestOnlyRequiredDependenciesFail:
    async def test_everything_up_is_ready(self, monkeypatch) -> None:
        _fake_probes(monkeypatch, **ALL_UP)
        report = await readiness.check()
        assert report.ready is True
        assert report.status == "ok"

    @pytest.mark.parametrize("down", ["postgres", "redis"])
    async def test_a_required_dependency_makes_it_unready(
        self, monkeypatch, down: str
    ) -> None:
        _fake_probes(monkeypatch, **{**ALL_UP, down: False})
        report = await readiness.check()
        assert report.ready is False
        assert report.status == "unready"
        assert report.checks[down] == readiness.UNAVAILABLE

    async def test_an_optional_dependency_only_degrades(self, monkeypatch) -> None:
        """THE POINT OF THIS FILE. The API still serves reads with MinIO down
        — and `job_reconcile` now ends orphaned jobs rather than leaving them
        spinning when no worker answers. Failing the probe here would take
        the UI down to report a partial outage.

        The worker half of this claim (down worker queues still degrade, not
        fail) is asserted with more precision in `TestPerQueueWorkerProbing`
        below, per queue rather than as one aggregate."""
        _fake_probes(monkeypatch, **{**ALL_UP, "minio": False})
        report = await readiness.check()
        assert report.ready is True, "minio must not make the instance unready"
        assert report.status == "degraded"
        assert report.checks["minio"] == readiness.UNAVAILABLE

    async def test_the_required_set_is_exactly_postgres_and_redis(self) -> None:
        """A guard on the decision itself, not on its consequences."""
        assert set(readiness.REQUIRED) == {"postgres", "redis"}

    async def test_every_dependency_the_gap_doc_lists_is_probed(self) -> None:
        assert set(readiness._PROBES) == {"postgres", "redis", "minio"}
        assert readiness.WORKER_QUEUES == ("gpu", "cpu")

    async def test_total_outage_is_unready_not_a_crash(self, monkeypatch) -> None:
        _fake_probes(monkeypatch, postgres=False, redis=False, minio=False)
        _fake_celery_inspect(monkeypatch, None)
        report = await readiness.check()
        assert report.ready is False
        assert set(report.checks.values()) == {readiness.UNAVAILABLE}


# =============================================================================
# 2. A probe must never become the outage
# =============================================================================


class TestProbesAreIsolatedAndBounded:
    async def test_a_hanging_probe_is_reported_not_awaited_forever(
        self, monkeypatch
    ) -> None:
        """A dependency that accepts the connection and then stops responding
        is the classic readiness trap: without a timeout the probe hangs, the
        orchestrator times out, and the instance is killed for a fault it was
        trying to report."""

        async def hang() -> None:
            await asyncio.sleep(3600)

        async def fine() -> None:
            return None

        monkeypatch.setattr(readiness, "PROBE_TIMEOUT_SECONDS", 0.05)
        monkeypatch.setattr(
            readiness,
            "_PROBES",
            {"postgres": fine, "redis": fine, "minio": hang},
        )
        report = await asyncio.wait_for(readiness.check(), timeout=5)
        assert report.checks["minio"] == readiness.UNAVAILABLE
        assert report.ready is True

    async def test_one_broken_probe_does_not_hide_the_others(
        self, monkeypatch
    ) -> None:
        _fake_probes(monkeypatch, **{**ALL_UP, "minio": False})
        report = await readiness.check()
        assert report.checks["postgres"] == readiness.OK
        assert report.checks["worker_gpu"] == readiness.OK
        assert report.checks["worker_cpu"] == readiness.OK

    async def test_probes_run_concurrently(self, monkeypatch) -> None:
        """Serially, the worst case is the sum of every timeout — and a
        readiness endpoint that takes several seconds is indistinguishable
        from a hung one. The worker-queue probe is gathered alongside the
        rest, not awaited separately, so it is included here too."""

        async def slow() -> None:
            await asyncio.sleep(0.2)

        async def slow_worker_queues() -> dict[str, bool]:
            await asyncio.sleep(0.2)
            return dict.fromkeys(readiness.WORKER_QUEUES, True)

        monkeypatch.setattr(readiness, "_PROBES", {n: slow for n in ALL_UP})
        monkeypatch.setattr(readiness, "probe_worker_queues", slow_worker_queues)
        loop = asyncio.get_running_loop()
        started = loop.time()
        await readiness.check()
        assert loop.time() - started < 0.6, "probes appear to be running in series"

    async def test_an_unexpected_error_type_still_reports(
        self, monkeypatch
    ) -> None:
        """The guard catches `Exception`, not just the connection errors the
        probes are expected to raise — a driver that fails in some novel way
        must still be reported as unavailable rather than 500 the endpoint."""

        async def explode() -> None:
            raise MemoryError("driver blew up in an unanticipated way")

        async def fine() -> None:
            return None

        monkeypatch.setattr(
            readiness,
            "_PROBES",
            {"postgres": fine, "redis": fine, "minio": explode},
        )
        report = await readiness.check()
        assert report.checks["minio"] == readiness.UNAVAILABLE


# =============================================================================
# 3. Per-queue worker probing — the dead-GPU-hides-behind-CPU fix
# =============================================================================


class TestPerQueueWorkerProbing:
    """THE FIX. A single Celery `ping()` only proves *some* node answered —
    on this deployment a CPU-only worker answers pings just fine while the
    GPU worker is dead, so the old aggregate `worker` check reported
    `worker: ok` straight through a GPU outage (proven live on the prod box).
    `inspect(...).active_queues()` instead names which queues each node
    actually consumes, so a dead GPU worker can no longer hide behind a
    healthy CPU one."""

    async def test_dead_gpu_worker_no_longer_hides_behind_cpu_worker(
        self, monkeypatch
    ) -> None:
        _fake_probes(monkeypatch, **ALL_UP)
        _fake_celery_inspect(monkeypatch, {"cpu-worker@host": [{"name": "cpu"}]})
        report = await readiness.check()
        assert report.checks["worker_gpu"] == readiness.UNAVAILABLE
        assert report.checks["worker_cpu"] == readiness.OK
        assert report.ready is True, "worker checks must never make the instance unready"
        assert report.status == "degraded"

    async def test_both_queues_served_are_both_ok(self, monkeypatch) -> None:
        _fake_probes(monkeypatch, **ALL_UP)
        # One node serving both queues is a valid, common topology — the
        # verdict is per queue name found in any reply, not per node.
        _fake_celery_inspect(
            monkeypatch, {"solo-worker@host": [{"name": "gpu"}, {"name": "cpu"}]}
        )
        report = await readiness.check()
        assert report.checks["worker_gpu"] == readiness.OK
        assert report.checks["worker_cpu"] == readiness.OK
        assert report.status == "ok"

    async def test_no_workers_answer_both_queues_unavailable_but_still_degraded(
        self, monkeypatch
    ) -> None:
        """`active_queues()` returns `None` when nobody answers — no workers
        running, or a broker issue. Both queues report unavailable, and the
        instance is still ready/200: submits still enqueue with no worker."""
        _fake_probes(monkeypatch, **ALL_UP)
        _fake_celery_inspect(monkeypatch, None)
        report = await readiness.check()
        assert report.checks["worker_gpu"] == readiness.UNAVAILABLE
        assert report.checks["worker_cpu"] == readiness.UNAVAILABLE
        assert report.ready is True
        assert report.status == "degraded"

    async def test_exactly_one_broadcast_per_check(self, monkeypatch) -> None:
        """Two queue verdicts must come from one `inspect(...).active_queues()`
        call, never one broadcast per queue — a second broadcast would double
        the network cost `WORKER_PROBE_TIMEOUT_SECONDS` already has to budget
        for."""
        _fake_probes(monkeypatch, **ALL_UP)
        inspector = _fake_celery_inspect(
            monkeypatch, {"worker@host": [{"name": "gpu"}, {"name": "cpu"}]}
        )
        await readiness.check()
        assert inspector.active_queues_calls == 1

    async def test_probe_worker_queues_is_directly_callable(self, monkeypatch) -> None:
        """A metrics adapter reuses this function directly, not only through
        `check()` — it must be public and usable standalone."""
        _fake_celery_inspect(monkeypatch, {"w@host": [{"name": "gpu"}]})
        served = await readiness.probe_worker_queues()
        assert served == {"gpu": True, "cpu": False}


# =============================================================================
# 4. The HTTP contract
# =============================================================================


class TestEndpointContract:
    async def test_body_names_every_dependency(self, monkeypatch) -> None:
        _fake_probes(monkeypatch, **ALL_UP)
        _fake_celery_inspect(monkeypatch, None)
        body = (await readiness.check()).as_dict()
        assert body["status"] == "degraded"
        assert body["checks"] == {
            "postgres": "ok",
            "redis": "ok",
            "minio": "ok",
            "worker_gpu": "unavailable",
            "worker_cpu": "unavailable",
        }

    def test_health_stays_dependency_free(self) -> None:
        """`/health` drives container restarts. If it ever starts probing
        dependencies, one Redis blip restarts an API that was serving fine."""
        from pathlib import Path

        import api.main as api_main

        src = Path(api_main.__file__).read_text(encoding="utf-8")
        health = src.split("async def health(")[1].split("async def ready(")[0]
        assert "readiness" not in health
        assert "return {\"status\": \"ok\"}" in health

    def test_ready_returns_503_only_when_unready(self) -> None:
        from pathlib import Path

        import api.main as api_main

        src = Path(api_main.__file__).read_text(encoding="utf-8")
        ready = src.split("async def ready(")[1]
        assert "if not report.ready:" in ready
        assert "HTTP_503_SERVICE_UNAVAILABLE" in ready


class TestWorkerProbeFitsInsideItsOwnBound:
    """REGRESSION GUARD for a bug that shipped green and only real infra showed.

    `probe_worker_queues` calls `celery_app.control.inspect(timeout=X)
    .active_queues()` inside a guard bounded at `PROBE_TIMEOUT_SECONDS`. The
    subtlety is that Celery's `inspect` does **not** return as soon as
    replies arrive — it waits out its whole timeout window collecting from
    every node, then adds broadcast and deserialisation cost on top. Measured
    on a two-worker box: inner=2.5s took 3.22s wall.

    The original `PROBE_TIMEOUT_SECONDS - 0.5` therefore overshot the 3s
    outer `wait_for` every single time, and `/ready` reported
    `worker: unavailable` — and the whole endpoint `degraded` — while both
    workers were answering the ping perfectly. Nothing in the unit suite
    could see it: the probe is monkeypatched everywhere else, so the only
    thing that ever exercised the real timing was a live box.

    Overhead scales with node count, so the margin must be proportional,
    not a fixed subtraction.
    """

    def test_inner_timeout_leaves_real_headroom(self) -> None:
        """Numeric, not a source-text match.

        The first draft of this guard asserted on `inspect.getsource(...)` and
        immediately matched the *comment* explaining the old bug rather than
        the code — a neat demonstration of why string assertions are the wrong
        tool. The measured overhead was 0.19-0.72s and grows with node count,
        so the inner window plus a generous overhead allowance must still fit
        inside the outer bound.
        """
        overhead_allowance = 1.0  # comfortably above the 0.72s seen live
        assert (
            readiness.WORKER_PROBE_TIMEOUT_SECONDS + overhead_allowance
            <= readiness.PROBE_TIMEOUT_SECONDS
        ), (
            f"inner window {readiness.WORKER_PROBE_TIMEOUT_SECONDS}s + "
            f"{overhead_allowance}s overhead exceeds the outer "
            f"{readiness.PROBE_TIMEOUT_SECONDS}s bound — `/ready` will report "
            f"a healthy worker as unavailable, which is exactly the bug this "
            f"guards against"
        )

    async def test_a_probe_that_takes_its_full_inner_window_still_passes(
        self, monkeypatch
    ) -> None:
        """Simulate the real timing: a worker-queue probe that burns its
        inner window plus overhead must still land inside
        `_guard_worker_queues`'s bound."""

        async def slow_but_successful_probe_worker_queues() -> dict[str, bool]:
            # Inner window + the measured ~0.6s overhead, as observed live.
            await asyncio.sleep(readiness.PROBE_TIMEOUT_SECONDS / 2 + 0.3)
            return dict.fromkeys(readiness.WORKER_QUEUES, True)

        monkeypatch.setattr(
            readiness, "probe_worker_queues", slow_but_successful_probe_worker_queues
        )
        report = await readiness.check()
        assert report.checks["worker_gpu"] == readiness.OK, (
            "a worker probe taking its full inner window plus overhead was "
            "reported unavailable — the margin is too thin again"
        )
        assert report.checks["worker_cpu"] == readiness.OK
