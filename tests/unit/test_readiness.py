"""`/ready` reports every dependency but only fails on the ones that matter.

BACKEND_GAP_ANALYSIS.md P1: "เพิ่ม `/ready` สำหรับตรวจ PostgreSQL, Redis, MinIO
และ worker; คง `/health` ไว้เป็น liveness check".

The interesting decision is not *which* dependencies get probed — the doc lists
them — but which ones make the probe fail. A readiness probe that 503s because
the Celery worker is down pulls the API out of its load balancer and takes the
whole UI offline, including every read that was working fine. That is a worse
outage than the one it reports. So Postgres and Redis are fatal (no request can
be served without them) and MinIO/worker degrade instead.

These tests exist mostly to stop that distinction being "simplified" later.
No real Postgres, Redis, MinIO or broker — every probe is monkeypatched.
"""

from __future__ import annotations

import asyncio

import pytest

from api.services import readiness


def _fake_probes(monkeypatch, **outcomes) -> None:
    """Replace the probe table with ones that succeed or raise on command.

    `outcomes` maps a dependency name to True (healthy) or False (down).
    """

    def make(ok: bool):
        async def probe() -> None:
            if not ok:
                raise ConnectionError("simulated outage")

        return probe

    monkeypatch.setattr(
        readiness, "_PROBES", {name: make(ok) for name, ok in outcomes.items()}
    )


ALL_UP = {"postgres": True, "redis": True, "minio": True, "worker": True}


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

    @pytest.mark.parametrize("down", ["minio", "worker"])
    async def test_an_optional_dependency_only_degrades(
        self, monkeypatch, down: str
    ) -> None:
        """THE POINT OF THIS FILE. The API still serves reads with MinIO down
        and still enqueues submits with no worker — and `job_reconcile` now
        ends orphaned jobs rather than leaving them spinning. Failing the probe
        here would take the UI down to report a partial outage."""
        _fake_probes(monkeypatch, **{**ALL_UP, down: False})
        report = await readiness.check()
        assert report.ready is True, f"{down} must not make the instance unready"
        assert report.status == "degraded"
        assert report.checks[down] == readiness.UNAVAILABLE

    async def test_the_required_set_is_exactly_postgres_and_redis(self) -> None:
        """A guard on the decision itself, not on its consequences."""
        assert set(readiness.REQUIRED) == {"postgres", "redis"}

    async def test_every_dependency_the_gap_doc_lists_is_probed(self) -> None:
        assert set(readiness._PROBES) == {"postgres", "redis", "minio", "worker"}

    async def test_total_outage_is_unready_not_a_crash(self, monkeypatch) -> None:
        _fake_probes(monkeypatch, postgres=False, redis=False, minio=False, worker=False)
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
            {"postgres": fine, "redis": fine, "minio": hang, "worker": fine},
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
        assert report.checks["worker"] == readiness.OK

    async def test_probes_run_concurrently(self, monkeypatch) -> None:
        """Serially, the worst case is the sum of four timeouts — and a
        readiness endpoint that takes 12 seconds is indistinguishable from a
        hung one."""

        async def slow() -> None:
            await asyncio.sleep(0.2)

        monkeypatch.setattr(readiness, "_PROBES", {n: slow for n in ALL_UP})
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
            {"postgres": fine, "redis": fine, "minio": explode, "worker": fine},
        )
        report = await readiness.check()
        assert report.checks["minio"] == readiness.UNAVAILABLE


# =============================================================================
# 3. The HTTP contract
# =============================================================================


class TestEndpointContract:
    async def test_body_names_every_dependency(self, monkeypatch) -> None:
        _fake_probes(monkeypatch, **{**ALL_UP, "worker": False})
        body = (await readiness.check()).as_dict()
        assert body["status"] == "degraded"
        assert body["checks"] == {
            "postgres": "ok",
            "redis": "ok",
            "minio": "ok",
            "worker": "unavailable",
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
