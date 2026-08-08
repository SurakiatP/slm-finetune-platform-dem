"""Unit tests for `api/services/metrics_sources.py`.

Layers covered:
  1. AST-scan  — proves the module never imports `prometheus_client`, the
     hard rule that lets this task run concurrently with the metrics-core
     task that wires these readers into an actual exporter.
  2. `queue_depths` — fakeredis-backed LLEN reads + fail-soft on a broken
     broker client.
  3. `job_counts` — sqlite-backed grouped counts, zero-row DB producing the
     full zero-filled (type, status) map, seeded rows producing correct
     counts, and fail-soft on a broken DB.
  4. `job_durations` — same DB fixture, seeded COMPLETED rows (including the
     started_at-null fallback-to-created_at case and the outside-the-24h-
     window exclusion case), and fail-soft on a broken DB.
  5. `breaker_state` — reuses `circuit_breaker`'s own Redis key/derivation,
     verified across closed/open/half_open, and fail-soft on broken Redis.
  6. `usage_totals` — grouped SUM(cost_usd)/SUM(prompt_tokens)/
     SUM(completion_tokens), and fail-soft on a broken DB.

Uses the sqlite JSONB `@compiles` shim pattern from
`tests/unit/test_dataset_status.py` (Dataset/TrainingJob use
`sqlalchemy.dialects.postgresql.JSONB`, which sqlite can't compile without
it) and `fakeredis` (already a dev dependency, same pattern as
`tests/unit/test_circuit_breaker.py`).
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import fakeredis
import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from api.models.base import Base
from api.models.dataset import Dataset
from api.models.evaluation_run import EvaluationRun
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.models.usage_event import UsageEvent
from api.schemas.enums import DatasetSource, JobStatus, TaskType, TrainingMode
from api.services import metrics_sources

# ---- KNOWN GOTCHA shim: JSONB is Postgres-only, sqlite needs a stand-in ----


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):
    return "JSON"


# =============================================================================
# 1. AST-scan — module import surface
# =============================================================================


def test_module_imports_something_and_never_prometheus_client() -> None:
    """Non-vacuity first (an empty import list would trivially pass a naive
    'no prometheus_client' check), then the actual hard-rule assertion."""
    source = Path(metrics_sources.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)

    assert imported_modules, "expected metrics_sources.py to import something"
    assert not any(
        mod == "prometheus_client" or mod.startswith("prometheus_client.")
        for mod in imported_modules
    ), f"metrics_sources.py must never import prometheus_client, found: {imported_modules}"


# =============================================================================
# 2. queue_depths
# =============================================================================


@pytest.fixture
def fake_broker_redis(monkeypatch: pytest.MonkeyPatch):
    """Patch `metrics_sources.from_url` (the broker-db client factory used
    by `queue_depths`) to hand back an in-memory fakeredis async client."""
    server = fakeredis.FakeServer()
    client = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    monkeypatch.setattr(metrics_sources, "from_url", lambda *a, **kw: client)
    return client


class TestQueueDepths:
    async def test_zero_when_queues_empty(self, fake_broker_redis) -> None:
        assert await metrics_sources.queue_depths() == {"gpu": 0, "cpu": 0}

    async def test_reflects_llen_per_queue(self, fake_broker_redis) -> None:
        await fake_broker_redis.rpush("gpu", "task-1", "task-2", "task-3")
        await fake_broker_redis.rpush("cpu", "task-4")
        assert await metrics_sources.queue_depths() == {"gpu": 3, "cpu": 1}

    async def test_broken_broker_returns_empty_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _ExplodingAsyncRedis:
            async def llen(self, *_a, **_kw):
                raise ConnectionError("broker is down")

            async def aclose(self) -> None:
                return None

        monkeypatch.setattr(metrics_sources, "from_url", lambda *a, **kw: _ExplodingAsyncRedis())
        assert await metrics_sources.queue_depths() == {}


# =============================================================================
# 3-4-6. DB-backed readers: job_counts, job_durations, usage_totals
# =============================================================================


@pytest.fixture
async def db_sessionmaker(monkeypatch: pytest.MonkeyPatch):
    """In-memory aiosqlite engine with the full ORM schema, wired in as
    `metrics_sources.AsyncSessionLocal` — each reader opens its own session
    off this the same way it opens one off the real `AsyncSessionLocal` in
    production."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(metrics_sources, "AsyncSessionLocal", maker)
    yield maker
    await engine.dispose()


def _all_job_count_keys() -> set[tuple[str, str]]:
    return {
        (job_type, status.value)
        for job_type in ("dataset", "training", "evaluation", "export")
        for status in JobStatus
    }


class TestJobCounts:
    async def test_zero_row_db_is_fully_zero_filled(self, db_sessionmaker) -> None:
        counts = await metrics_sources.job_counts()
        assert set(counts.keys()) == _all_job_count_keys()
        assert all(v == 0 for v in counts.values())

    async def test_seeded_rows_produce_correct_counts(self, db_sessionmaker) -> None:
        async with db_sessionmaker() as session:
            project = Project(id=uuid4(), name="p", task_type=TaskType.QA)
            session.add(project)
            await session.flush()

            ds_pending = Dataset(
                id=uuid4(),
                project_id=project.id,
                name="ds1",
                task_type=TaskType.QA,
                source=DatasetSource.SDG,
                status=JobStatus.PENDING,
                num_samples=0,
            )
            ds_pending_2 = Dataset(
                id=uuid4(),
                project_id=project.id,
                name="ds2",
                task_type=TaskType.QA,
                source=DatasetSource.SDG,
                status=JobStatus.PENDING,
                num_samples=0,
            )
            ds_completed = Dataset(
                id=uuid4(),
                project_id=project.id,
                name="ds3",
                task_type=TaskType.QA,
                source=DatasetSource.SEED,
                status=JobStatus.COMPLETED,
                num_samples=1,
            )
            session.add_all([ds_pending, ds_pending_2, ds_completed])
            await session.flush()

            training = TrainingJob(
                id=uuid4(),
                project_id=project.id,
                dataset_id=ds_completed.id,
                mode=TrainingMode.MANUAL,
                status=JobStatus.RUNNING,
                base_model="unsloth/Llama-3.2-3B-Instruct-bnb-4bit",
                config_json={},
            )
            session.add(training)
            await session.flush()

            artifact_with_export = ModelArtifact(
                id=uuid4(),
                training_job_id=training.id,
                name="art1",
                base_model="unsloth/Llama-3.2-3B-Instruct-bnb-4bit",
                export_status=JobStatus.COMPLETED,
            )
            session.add(artifact_with_export)
            await session.flush()

            evaluation = EvaluationRun(
                id=uuid4(),
                model_artifact_id=artifact_with_export.id,
                dataset_id=ds_completed.id,
                status=JobStatus.FAILED,
            )
            session.add(evaluation)

            # A second training job whose artifact has never had an export
            # requested (export_status=None) — must NOT be counted anywhere,
            # including not as a phantom "export: none" bucket.
            training_2 = TrainingJob(
                id=uuid4(),
                project_id=project.id,
                dataset_id=ds_completed.id,
                mode=TrainingMode.MANUAL,
                status=JobStatus.COMPLETED,
                base_model="unsloth/Llama-3.2-3B-Instruct-bnb-4bit",
                config_json={},
            )
            session.add(training_2)
            await session.flush()
            artifact_no_export = ModelArtifact(
                id=uuid4(),
                training_job_id=training_2.id,
                name="art2",
                base_model="unsloth/Llama-3.2-3B-Instruct-bnb-4bit",
                export_status=None,
            )
            session.add(artifact_no_export)

            await session.commit()

        counts = await metrics_sources.job_counts()
        assert set(counts.keys()) == _all_job_count_keys()
        assert counts[("dataset", "pending")] == 2
        assert counts[("dataset", "completed")] == 1
        assert counts[("dataset", "running")] == 0
        assert counts[("training", "running")] == 1
        assert counts[("training", "completed")] == 1
        assert counts[("evaluation", "failed")] == 1
        assert counts[("evaluation", "pending")] == 0
        assert counts[("export", "completed")] == 1
        # export_status=None on artifact_no_export contributes to no bucket.
        assert sum(v for (t, _s), v in counts.items() if t == "export") == 1

    async def test_broken_db_returns_empty_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*_a, **_kw):
            raise RuntimeError("db is down")

        monkeypatch.setattr(metrics_sources, "AsyncSessionLocal", _boom)
        assert await metrics_sources.job_counts() == {}


class TestJobDurations:
    async def test_seeded_rows_produce_correct_avg_and_max(self, db_sessionmaker) -> None:
        now = datetime.now(UTC)
        async with db_sessionmaker() as session:
            project = Project(id=uuid4(), name="p", task_type=TaskType.QA)
            session.add(project)
            await session.flush()

            # Dataset: no started_at column -> created_at is the start.
            # 10s duration, well inside the 24h window.
            ds = Dataset(
                id=uuid4(),
                project_id=project.id,
                name="ds",
                task_type=TaskType.QA,
                source=DatasetSource.SEED,
                status=JobStatus.COMPLETED,
                num_samples=1,
                created_at=now - timedelta(seconds=10),
                updated_at=now,
            )
            session.add(ds)
            await session.flush()

            # TrainingJob #1: started_at set explicitly -> 100s duration.
            training_1 = TrainingJob(
                id=uuid4(),
                project_id=project.id,
                dataset_id=ds.id,
                mode=TrainingMode.MANUAL,
                status=JobStatus.COMPLETED,
                base_model="m",
                config_json={},
                started_at=now - timedelta(seconds=100),
                created_at=now - timedelta(seconds=500),  # must be ignored in favor of started_at
                updated_at=now,
            )
            # TrainingJob #2: started_at is null -> falls back to created_at,
            # a 50s duration.
            training_2 = TrainingJob(
                id=uuid4(),
                project_id=project.id,
                dataset_id=ds.id,
                mode=TrainingMode.MANUAL,
                status=JobStatus.COMPLETED,
                base_model="m",
                config_json={},
                started_at=None,
                created_at=now - timedelta(seconds=50),
                updated_at=now,
            )
            # TrainingJob #3: COMPLETED but its updated_at is outside the
            # trailing 24h window -> must be excluded entirely.
            training_3 = TrainingJob(
                id=uuid4(),
                project_id=project.id,
                dataset_id=ds.id,
                mode=TrainingMode.MANUAL,
                status=JobStatus.COMPLETED,
                base_model="m",
                config_json={},
                started_at=now - timedelta(hours=48, seconds=5),
                created_at=now - timedelta(hours=48, seconds=5),
                updated_at=now - timedelta(hours=48),
            )
            session.add_all([training_1, training_2, training_3])
            await session.commit()

        durations = await metrics_sources.job_durations()

        dataset_avg, dataset_max = durations["dataset"]
        assert dataset_avg == pytest.approx(10.0)
        assert dataset_max == pytest.approx(10.0)

        training_avg, training_max = durations["training"]
        # Only training_1 (100s) and training_2 (50s, via created_at
        # fallback) count; training_3 is outside the window.
        assert training_avg == pytest.approx((100.0 + 50.0) / 2)
        assert training_max == pytest.approx(100.0)

        # No evaluation/export rows seeded at all -> absent, not zero.
        assert "evaluation" not in durations
        assert "export" not in durations

    async def test_broken_db_returns_empty_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*_a, **_kw):
            raise RuntimeError("db is down")

        monkeypatch.setattr(metrics_sources, "AsyncSessionLocal", _boom)
        assert await metrics_sources.job_durations() == {}


class TestUsageTotals:
    async def test_zero_row_db_is_empty(self, db_sessionmaker) -> None:
        assert await metrics_sources.usage_totals() == {}

    async def test_seeded_rows_grouped_correctly(self, db_sessionmaker) -> None:
        async with db_sessionmaker() as session:
            session.add_all(
                [
                    UsageEvent(
                        id=uuid4(),
                        provider="openrouter",
                        model="qwen/qwen3-235b-a22b-2507",
                        stage="sdg",
                        prompt_tokens=100,
                        completion_tokens=50,
                        cost_usd="0.01",
                        outcome="completed",
                    ),
                    UsageEvent(
                        id=uuid4(),
                        provider="openrouter",
                        model="qwen/qwen3-235b-a22b-2507",
                        stage="sdg",
                        prompt_tokens=200,
                        completion_tokens=75,
                        cost_usd="0.02",
                        outcome="completed",
                    ),
                    UsageEvent(
                        id=uuid4(),
                        provider="openrouter",
                        model="qwen/qwen3-235b-a22b-2507",
                        stage="sdg",
                        prompt_tokens=10,
                        completion_tokens=0,
                        cost_usd=None,
                        outcome="failed",
                    ),
                ]
            )
            await session.commit()

        totals = await metrics_sources.usage_totals()
        completed = totals[("qwen/qwen3-235b-a22b-2507", "sdg", "completed")]
        assert completed["cost_usd"] == pytest.approx(0.03)
        assert completed["prompt_tokens"] == 300
        assert completed["completion_tokens"] == 125

        failed = totals[("qwen/qwen3-235b-a22b-2507", "sdg", "failed")]
        # SUM() over an all-null cost_usd group must report 0.0, not None.
        assert failed["cost_usd"] == 0.0
        assert failed["prompt_tokens"] == 10
        assert failed["completion_tokens"] == 0

    async def test_broken_db_returns_empty_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*_a, **_kw):
            raise RuntimeError("db is down")

        monkeypatch.setattr(metrics_sources, "AsyncSessionLocal", _boom)
        assert await metrics_sources.usage_totals() == {}


# =============================================================================
# 5. breaker_state
# =============================================================================


@pytest.fixture
def fake_breaker_redis(monkeypatch: pytest.MonkeyPatch):
    """Same shared-server pattern as `test_circuit_breaker.py`'s
    `fake_breaker_redis`: patch `metrics_sources.get_redis_client` (the
    factory `breaker_state` calls) to hand back a fakeredis async client
    pointed at a fresh in-memory server."""
    server = fakeredis.FakeServer()
    client = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    monkeypatch.setattr(metrics_sources, "get_redis_client", lambda: client)
    return client


class TestBreakerState:
    async def test_no_opened_at_key_is_closed(self, fake_breaker_redis) -> None:
        assert await metrics_sources.breaker_state() == 0

    async def test_recently_opened_is_open(self, fake_breaker_redis) -> None:
        import time

        from api.services import circuit_breaker as cb

        await fake_breaker_redis.set(cb._OPENED_AT_KEY, str(time.time()))
        assert await metrics_sources.breaker_state() == 2

    async def test_opened_past_window_is_half_open(self, fake_breaker_redis) -> None:
        import time

        from api.core.config import get_settings
        from api.services import circuit_breaker as cb

        settings = get_settings()
        past = time.time() - settings.openrouter_breaker_open_seconds - 1
        await fake_breaker_redis.set(cb._OPENED_AT_KEY, str(past))
        assert await metrics_sources.breaker_state() == 1

    async def test_broken_redis_degrades_to_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _ExplodingAsyncRedis:
            async def get(self, *_a, **_kw):
                raise ConnectionError("redis is down")

            async def aclose(self) -> None:
                return None

        monkeypatch.setattr(metrics_sources, "get_redis_client", lambda: _ExplodingAsyncRedis())
        assert await metrics_sources.breaker_state() == 0
