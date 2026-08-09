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
  7. `classify_error` — one seeded signature per `ERROR_TYPES` member
     (including the CANCELLED-status-wins-over-text "-241" case and the
     `job_reconcile` orphan sentence, matched by text not by
     `job_reconcile._ERROR_TYPE`'s "OrphanedJob"), a corpus/property test
     that the function's output is always a member of `ERROR_TYPES` no
     matter the input, and `job_failure_counts`'s zero-fill + fail-soft
     behavior over the same sqlite fixture the other DB-backed readers use.

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


# =============================================================================
# 7. classify_error / job_failure_counts
# =============================================================================


class TestClassifyErrorSignatures:
    """One real signature per `ERROR_TYPES` member, each named explicitly.

    Every message string here is copied verbatim (or near-verbatim, where a
    caller interpolates a value) from the code path that actually produces
    it — not invented — so this test doubles as a check that the classifier
    still matches the *current* wording of each producer.
    """

    def test_cancelled_status_wins_even_with_the_dash_241_sentinel(self) -> None:
        # api/services/job_control.py: a cancelled task's SIGTERM surfaces as
        # SystemExit(-241) inside the task's `except BaseException` handler,
        # and `error_message` ends up literally "-241" — no human-readable
        # signature at all. Status is what must decide this one.
        assert metrics_sources.classify_error(JobStatus.CANCELLED, "-241") == "cancelled"

    def test_cancelled_status_wins_over_a_matching_text_signature_too(self) -> None:
        # Belt-and-suspenders: CANCELLED must win even when the leftover
        # error text *would* otherwise match a different signature.
        assert (
            metrics_sources.classify_error(JobStatus.CANCELLED, "CUDA out of memory") == "cancelled"
        )

    def test_orphaned_matches_job_reconcile_sentence_not_the_frame_error_type(self) -> None:
        # api/services/job_reconcile.py's `reconcile_once` writes this exact
        # free-text sentence (abbreviated here) into the DB error column.
        # `job_reconcile._ERROR_TYPE` ("OrphanedJob") never appears in the DB
        # — it only travels in the transient WebSocket JobFailed frame — so a
        # classifier matching "OrphanedJob" would match zero real rows.
        message = (
            "No worker is executing this job and it has published nothing "
            "since 2026-08-08T10:00:00+00:00 (via updated_at, grace 30m). "
            "The worker running Celery task abc-123 most likely died."
        )
        assert metrics_sources.classify_error(JobStatus.FAILED, message) == "orphaned"
        # The frame-only string must NOT match on its own.
        assert metrics_sources.classify_error(JobStatus.FAILED, "OrphanedJob") != "orphaned"

    def test_oom_matches_cuda_out_of_memory_case_insensitive(self) -> None:
        # No code in this repo raises this itself — it comes straight from
        # PyTorch/CUDA during training.
        message = "CUDA out of memory. Tried to allocate 20.00 MiB (GPU 0; 12.00 GiB total capacity)"
        assert metrics_sources.classify_error(JobStatus.FAILED, message) == "oom"
        assert metrics_sources.classify_error(JobStatus.FAILED, "torch.cuda.OutOfMemoryError") == "oom"

    def test_provider_matches_openrouter_circuit_breaker_open(self) -> None:
        # api/services/circuit_breaker.py's CircuitOpenError message.
        message = "OpenRouter circuit breaker open; retry after 30s"
        assert metrics_sources.classify_error(JobStatus.FAILED, message) == "provider"

    def test_provider_matches_current_openrouter_budget_exceeded_wording(self) -> None:
        # ai_engine/data_gen/usage.py's SDGBudgetExceededError. The wording
        # was changed from the class's own historical "SDG budget exceeded"
        # to "OpenRouter budget exceeded" — the classifier must match the
        # CURRENT message, not the stale one.
        message = "OpenRouter budget exceeded: spent $5.0000 of $0.0000 remaining budget"
        assert metrics_sources.classify_error(JobStatus.FAILED, message) == "provider"

    def test_provider_matches_ollama_daemon_failure(self) -> None:
        # workers/ollama_client.py's OllamaError.
        message = "ollama upload_blob failed: connection refused"
        assert metrics_sources.classify_error(JobStatus.FAILED, message) == "provider"

    def test_storage_matches_minio_s3_bucket_text(self) -> None:
        # minio-py's own S3Error text always includes "bucket_name:"; a
        # connection failure to the in-network endpoint mentions "minio:9000"
        # verbatim; workers/storage.py::parse_s3_uri's ValueErrors start
        # "s3 URI ...".
        message = (
            "S3 operation failed; code: NoSuchBucket, message: The specified "
            "bucket does not exist, bucket_name: model-artifacts"
        )
        assert metrics_sources.classify_error(JobStatus.FAILED, message) == "storage"
        assert metrics_sources.classify_error(JobStatus.FAILED, "s3 URI missing object key: 'x'") == "storage"

    def test_other_is_the_fallback_for_unmatched_text(self) -> None:
        message = "ValueError: unexpected token at position 4 while parsing config"
        assert metrics_sources.classify_error(JobStatus.FAILED, message) == "other"


class TestClassifyErrorCorpus:
    """Property-style check: whatever goes in, the output is always a
    member of `ERROR_TYPES` — the whole reason the whitelist is closed."""

    _REAL_MESSAGES = (
        "-241",
        "No worker is executing this job and it has published nothing since "
        "2026-08-08T10:00:00+00:00 (via snapshot, grace 30m). The worker "
        "running Celery task xyz most likely died.",
        "CUDA out of memory. Tried to allocate 512.00 MiB",
        "torch.cuda.OutOfMemoryError: CUDA out of memory.",
        "OpenRouter circuit breaker open; retry after 60s",
        "OpenRouter budget exceeded: spent $1.2345 of $0.0000 remaining budget",
        "ollama create failed: model manifest not found",
        "ollama upload_blob failed: EOF",
        "S3 operation failed; code: AccessDenied, message: Access Denied, "
        "bucket_name: datasets",
        "s3 URI has empty bucket or key: 's3:///'",
        "expected s3:// URI, got: 'http://example.com'",
        "MINIO_PUBLIC_URL is not set — cannot mint presigned download URLs "
        "on this deployment.",
        "json.decoder.JSONDecodeError: Expecting value: line 1 column 1",
        "ConnectionRefusedError: [Errno 111] Connection refused",
        "",
    )

    _JUNK_MESSAGES = (None, "", "   ", "🔥💥", "a" * 5000, 12345, object())

    _STATUSES = (
        JobStatus.PENDING,
        JobStatus.RUNNING,
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
        "failed",
        "CANCELLED",
        "cancelled",
        None,
        "",
        "totally-not-a-status",
        object(),
    )

    def test_real_messages_classify_into_the_whitelist(self) -> None:
        for message in self._REAL_MESSAGES:
            for status in (JobStatus.FAILED, JobStatus.CANCELLED):
                result = metrics_sources.classify_error(status, message)
                assert result in metrics_sources.ERROR_TYPES, (status, message, result)

    def test_junk_and_none_input_still_classifies_into_the_whitelist(self) -> None:
        for status in self._STATUSES:
            for message in self._JUNK_MESSAGES:
                result = metrics_sources.classify_error(status, message)  # type: ignore[arg-type]
                assert result in metrics_sources.ERROR_TYPES, (status, message, result)

    def test_error_types_is_the_exact_closed_whitelist(self) -> None:
        assert metrics_sources.ERROR_TYPES == (
            "oom",
            "provider",
            "storage",
            "cancelled",
            "orphaned",
            "other",
        )


def _all_job_failure_keys() -> set[tuple[str, str]]:
    return {
        (job_type, error_type)
        for job_type in ("dataset", "training", "evaluation", "export")
        for error_type in metrics_sources.ERROR_TYPES
    }


class TestJobFailureCounts:
    async def test_zero_row_db_is_fully_zero_filled(self, db_sessionmaker) -> None:
        counts = await metrics_sources.job_failure_counts()
        assert set(counts.keys()) == _all_job_failure_keys()
        assert all(v == 0 for v in counts.values())

    async def test_seeded_rows_land_in_the_right_bucket(self, db_sessionmaker) -> None:
        async with db_sessionmaker() as session:
            project = Project(id=uuid4(), name="p", task_type=TaskType.QA)
            session.add(project)
            await session.flush()

            # dataset: cancelled export-style sentinel ("-241").
            ds_cancelled = Dataset(
                id=uuid4(),
                project_id=project.id,
                name="ds-cancelled",
                task_type=TaskType.QA,
                source=DatasetSource.SDG,
                status=JobStatus.CANCELLED,
                num_samples=0,
                error_message="-241",
            )
            # dataset: OpenRouter budget exceeded (current wording).
            ds_provider = Dataset(
                id=uuid4(),
                project_id=project.id,
                name="ds-provider",
                task_type=TaskType.QA,
                source=DatasetSource.SDG,
                status=JobStatus.FAILED,
                num_samples=0,
                error_message=(
                    "OpenRouter budget exceeded: spent $5.0000 of $0.0000 remaining budget"
                ),
            )
            # dataset: a row that later succeeded on retry but still carries
            # a stale error_message (Dataset never clears it) — must still
            # be counted, per job_failure_counts' "IS NOT NULL, not status"
            # filter.
            ds_stale_after_retry = Dataset(
                id=uuid4(),
                project_id=project.id,
                name="ds-stale",
                task_type=TaskType.QA,
                source=DatasetSource.SDG,
                status=JobStatus.COMPLETED,
                num_samples=1,
                error_message="ollama create failed: earlier attempt",
            )
            session.add_all([ds_cancelled, ds_provider, ds_stale_after_retry])
            await session.flush()

            # training: orphan-reconcile sentence.
            training_orphaned = TrainingJob(
                id=uuid4(),
                project_id=project.id,
                dataset_id=ds_stale_after_retry.id,
                mode=TrainingMode.MANUAL,
                status=JobStatus.FAILED,
                base_model="m",
                config_json={},
                error_message=(
                    "No worker is executing this job and it has published nothing "
                    "since 2026-08-08T09:00:00+00:00 (via updated_at, grace 30m). "
                    "The worker running Celery task abc most likely died."
                ),
            )
            # training: CUDA OOM.
            training_oom = TrainingJob(
                id=uuid4(),
                project_id=project.id,
                dataset_id=ds_stale_after_retry.id,
                mode=TrainingMode.MANUAL,
                status=JobStatus.FAILED,
                base_model="m",
                config_json={},
                error_message="CUDA out of memory. Tried to allocate 1.00 GiB",
            )
            session.add_all([training_orphaned, training_oom])
            await session.flush()

            # evaluation: unmatched text -> other.
            artifact = ModelArtifact(
                id=uuid4(),
                training_job_id=training_oom.id,
                name="art",
                base_model="m",
                export_status=JobStatus.FAILED,
                export_error_message=(
                    "S3 operation failed; code: NoSuchBucket, message: gone, "
                    "bucket_name: model-artifacts"
                ),
            )
            session.add(artifact)
            await session.flush()

            evaluation_other = EvaluationRun(
                id=uuid4(),
                model_artifact_id=artifact.id,
                dataset_id=ds_stale_after_retry.id,
                status=JobStatus.FAILED,
                error_message="ValueError: unexpected token while parsing config",
            )
            session.add(evaluation_other)

            await session.commit()

        counts = await metrics_sources.job_failure_counts()
        assert set(counts.keys()) == _all_job_failure_keys()

        assert counts[("dataset", "cancelled")] == 1
        assert counts[("dataset", "other")] == 0  # none seeded
        # ds_stale_after_retry (COMPLETED, "ollama create failed: ...") still
        # counts, into "provider" — its status is COMPLETED, not CANCELLED.
        assert counts[("dataset", "provider")] == 2  # ds_provider + ds_stale_after_retry
        assert sum(v for (t, e), v in counts.items() if t == "dataset") == 3

        assert counts[("training", "orphaned")] == 1
        assert counts[("training", "oom")] == 1
        assert sum(v for (t, e), v in counts.items() if t == "training") == 2

        assert counts[("evaluation", "other")] == 1
        assert sum(v for (t, e), v in counts.items() if t == "evaluation") == 1

        assert counts[("export", "storage")] == 1
        assert sum(v for (t, e), v in counts.items() if t == "export") == 1

    async def test_broken_db_returns_empty_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*_a, **_kw):
            raise RuntimeError("db is down")

        monkeypatch.setattr(metrics_sources, "AsyncSessionLocal", _boom)
        assert await metrics_sources.job_failure_counts() == {}
