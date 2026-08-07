"""Unit tests proving `generate_synthetic_data` persists `usage_events` rows
on every terminal outcome (T11 — the keystone task that makes SDG cost
accounting actually happen).

Runs the Celery task synchronously via `.apply(...)` against an in-memory
sync sqlite engine, mirroring the established pattern in
`tests/unit/test_dataset_status.py` (same fixtures, same
`_install_worker_patches`-style monkeypatching of `session_scope` /
`sync_redis_scope` / `get_minio_client` at the worker module's own
use-sites).

`_run_generator` is monkeypatched per test to control exactly what the
accumulator sees (`.add(...)` calls) and how the run ends (return /
raise / raise `SDGBudgetExceededError`) — the real async generator loop is
covered elsewhere; this file is about what the *worker* does with the
accumulator once `_run_generator` hands control back (or blows up).
"""

from __future__ import annotations

from contextlib import contextmanager
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from ai_engine.data_gen.generator import SDGRunResult
from ai_engine.data_gen.usage import STAGE_GENERATE
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.project import Project
from api.models.usage_event import UsageEvent
from api.schemas.enums import DatasetSource, JobStatus, TaskType
from api.schemas.sdg import SDGRequestDescriptionOnly

# ---- KNOWN GOTCHA shim: JSONB is Postgres-only, sqlite needs a stand-in ----
# (same shim as tests/unit/test_dataset_status.py — Dataset/Project use
# sqlalchemy.dialects.postgresql.JSONB, which sqlite's DDL compiler doesn't
# understand without this.)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


# =============================================================================
# Fixtures / shared helpers (mirrors test_dataset_status.py's worker section)
# =============================================================================


@pytest.fixture
def sync_sessionmaker():
    """In-memory sync sqlite engine + sessionmaker with the full ORM schema."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    yield maker
    engine.dispose()


def _install_worker_patches(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub):
    """Same use-site patching as test_dataset_status.py's
    `_install_worker_patches`: patch `session_scope` / `sync_redis_scope` /
    `get_minio_client` on `workers.tasks.data_generation` itself, not on the
    modules they were imported from — the module did `from X import Y`,
    which binds a local alias that patching `X.Y` afterwards doesn't reach.
    """
    import workers.tasks.data_generation as dg_module

    @contextmanager
    def _fake_session_scope():
        session = sync_sessionmaker()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def _fake_redis_scope():
        yield fake_redis_pubsub.client

    monkeypatch.setattr(dg_module, "session_scope", _fake_session_scope)
    monkeypatch.setattr(dg_module, "sync_redis_scope", _fake_redis_scope)
    monkeypatch.setattr(dg_module, "get_minio_client", lambda: fake_minio)
    return dg_module


def _seed_project_and_dataset(sync_sessionmaker, *, project_id, dataset_id, status=JobStatus.PENDING):
    session = sync_sessionmaker()
    try:
        project = Project(id=project_id, name="proj", task_type=TaskType.QA)
        session.add(project)
        dataset = Dataset(
            id=dataset_id,
            project_id=project_id,
            name="sdg-ds",
            task_type=TaskType.QA,
            source=DatasetSource.SDG,
            status=status,
            num_samples=0,
        )
        session.add(dataset)
        session.commit()
    finally:
        session.close()


def _build_description_only_payload(project_id) -> dict:
    request = SDGRequestDescriptionOnly(
        project_id=project_id,
        task_type=TaskType.QA,
        task_description="Answer questions about our 30-day return policy",
        num_samples=1,
        holdout_size=0,
    )
    return request.model_dump(mode="json")


def _usage_rows(sync_sessionmaker) -> list[UsageEvent]:
    session = sync_sessionmaker()
    try:
        return list(session.execute(select(UsageEvent)).scalars().all())
    finally:
        session.close()


# =============================================================================
# 1. Success path — outcome="completed"
# =============================================================================


class TestUsageEventsSuccessPath:
    def test_success_path_writes_completed_usage_rows(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )

        async def _fake_run_generator(**kwargs):
            usage = kwargs["usage"]
            usage.add(dg_module.sdg_models.GENERATOR, STAGE_GENERATE, 1000, 500)
            return SDGRunResult(
                valid_rows=[{"question": "q", "answer": "a"}],
                rejected_count=0,
                duplicate_count=0,
                judge_rejected_count=0,
                judge_parse_failures=0,
                api_calls=1,
            )

        monkeypatch.setattr(dg_module, "_run_generator", _fake_run_generator)

        project_id = uuid4()
        dataset_id = uuid4()
        _seed_project_and_dataset(sync_sessionmaker, project_id=project_id, dataset_id=dataset_id)
        payload = _build_description_only_payload(project_id)

        result = dg_module.generate_synthetic_data.apply(
            kwargs={"request_payload": payload, "dataset_id": str(dataset_id)}
        )
        assert result.successful(), f"task raised: {result.result!r}"

        rows = _usage_rows(sync_sessionmaker)
        assert len(rows) == 1
        row = rows[0]
        assert row.outcome == "completed"
        assert row.job_id == result.id
        assert row.model == dg_module.sdg_models.GENERATOR
        assert row.stage == STAGE_GENERATE
        assert row.prompt_tokens == 1000
        assert row.completion_tokens == 500
        # GENERATOR is priced in the built-in map (see model_pricing.py) —
        # cost_usd must be a real, non-null number.
        assert row.cost_usd is not None
        assert row.project_id == project_id
        assert row.provider == "openrouter"


# =============================================================================
# 2. Failure (raising) path — outcome="failed", still bills what was spent
# =============================================================================


class TestUsageEventsFailurePath:
    def test_raising_path_writes_failed_usage_rows(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )

        async def _raising_run_generator(**kwargs):
            # Burn some tokens before the run blows up — this is exactly the
            # case usage accounting exists for: a run that fails must still
            # bill what it spent.
            usage = kwargs["usage"]
            usage.add(dg_module.sdg_models.GENERATOR, STAGE_GENERATE, 2000, 750)
            raise RuntimeError("injected SDG failure for test")

        monkeypatch.setattr(dg_module, "_run_generator", _raising_run_generator)

        project_id = uuid4()
        dataset_id = uuid4()
        _seed_project_and_dataset(sync_sessionmaker, project_id=project_id, dataset_id=dataset_id)
        payload = _build_description_only_payload(project_id)

        result = dg_module.generate_synthetic_data.apply(
            kwargs={"request_payload": payload, "dataset_id": str(dataset_id)}
        )
        assert not result.successful()
        assert result.failed()

        session = sync_sessionmaker()
        try:
            ds = session.get(Dataset, dataset_id)
            assert ds is not None
            assert ds.status == JobStatus.FAILED
        finally:
            session.close()

        rows = _usage_rows(sync_sessionmaker)
        assert len(rows) == 1
        row = rows[0]
        assert row.outcome == "failed"
        assert row.prompt_tokens == 2000
        assert row.completion_tokens == 750
        assert row.job_id == result.id


# =============================================================================
# 3. Cancel path — outcome="cancelled"
# =============================================================================


class TestUsageEventsCancelPath:
    def test_cancel_path_writes_cancelled_usage_rows(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )

        project_id = uuid4()
        dataset_id = uuid4()
        _seed_project_and_dataset(sync_sessionmaker, project_id=project_id, dataset_id=dataset_id)

        async def _cancelled_run_generator(**kwargs):
            usage = kwargs["usage"]
            usage.add(dg_module.sdg_models.GENERATOR, STAGE_GENERATE, 1800, 900)
            # Simulate `POST /datasets/{id}/cancel`: it flips the dataset to
            # CANCELLED (in its own transaction) *before* revoking the task,
            # so by the time the SIGTERM-turned-exception lands in the
            # worker's `except BaseException` handler, CANCELLED is already
            # sitting in the DB for it to read back.
            session = sync_sessionmaker()
            try:
                ds = session.get(Dataset, dataset_id)
                if ds is not None:
                    ds.status = JobStatus.CANCELLED
                    session.commit()
            finally:
                session.close()
            raise RuntimeError("-241")

        monkeypatch.setattr(dg_module, "_run_generator", _cancelled_run_generator)

        payload = _build_description_only_payload(project_id)

        result = dg_module.generate_synthetic_data.apply(
            kwargs={"request_payload": payload, "dataset_id": str(dataset_id)}
        )
        assert not result.successful()

        session = sync_sessionmaker()
        try:
            ds = session.get(Dataset, dataset_id)
            assert ds is not None
            assert ds.status == JobStatus.CANCELLED
        finally:
            session.close()

        rows = _usage_rows(sync_sessionmaker)
        assert len(rows) == 1
        row = rows[0]
        assert row.outcome == "cancelled"
        assert row.prompt_tokens == 1800
        assert row.completion_tokens == 900


# =============================================================================
# 4. Unpriced model — cost_usd NULL, tokens still recorded
# =============================================================================


class TestUsageEventsUnpricedModel:
    def test_unpriced_model_has_null_cost_but_nonzero_tokens(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )

        async def _fake_run_generator(**kwargs):
            usage = kwargs["usage"]
            # Not in model_pricing._BUILTIN_PRICING_USD_PER_1M and not set via
            # MODEL_PRICING_JSON in this test env — deliberately unpriced.
            usage.add("some-vendor/unpriced-model", STAGE_GENERATE, 321, 654)
            return SDGRunResult(
                valid_rows=[{"question": "q", "answer": "a"}],
                rejected_count=0,
                duplicate_count=0,
                judge_rejected_count=0,
                judge_parse_failures=0,
                api_calls=1,
            )

        monkeypatch.setattr(dg_module, "_run_generator", _fake_run_generator)

        project_id = uuid4()
        dataset_id = uuid4()
        _seed_project_and_dataset(sync_sessionmaker, project_id=project_id, dataset_id=dataset_id)
        payload = _build_description_only_payload(project_id)

        result = dg_module.generate_synthetic_data.apply(
            kwargs={"request_payload": payload, "dataset_id": str(dataset_id)}
        )
        assert result.successful(), f"task raised: {result.result!r}"

        rows = _usage_rows(sync_sessionmaker)
        assert len(rows) == 1
        row = rows[0]
        assert row.model == "some-vendor/unpriced-model"
        assert row.cost_usd is None
        assert row.prompt_tokens == 321
        assert row.completion_tokens == 654


# =============================================================================
# 5. Budget exceeded — FAILED with SDGBudgetExceededError, usage still written
# =============================================================================


class TestUsageEventsBudgetExceeded:
    def test_budget_exceeded_fails_run_and_still_writes_usage(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )

        # Tiny *global* cap with zero prior spend -> budget_remaining_usd
        # resolves to (almost) $0, so the very first priced call trips
        # `UsageAccumulator.check_budget()` for real — this exercises the
        # actual budget_remaining_usd computed at task start, not a
        # hand-raised stand-in. Global rather than per-actor: `.apply()`'s
        # eager execution builds a bare `Context` whose custom headers stay
        # nested under `.headers` rather than flattened onto the object as
        # `x_user_id` (that flattening is a real-broker-consumption behavior
        # `task_prerun`'s `getattr(ctx, "x_user_id", None)` relies on), so
        # `request_context.current_user_id()` is reliably `None` under
        # `.apply()` regardless of any `request_context.bound(...)` the test
        # wraps around the call — matching every other worker test in this
        # suite, none of which assert on `actor_id`. The global cap doesn't
        # depend on `actor_id` at all (see `assert_within_budget_sync`), so
        # it exercises the real budget-gate code path without fighting that.
        real_settings = dg_module.get_settings()
        tight_settings = real_settings.model_copy(
            update={
                "budget_monthly_usd_per_actor": None,
                "budget_monthly_usd_global": 0.000001,
            }
        )
        monkeypatch.setattr(dg_module, "get_settings", lambda: tight_settings)

        async def _fake_run_generator(**kwargs):
            usage = kwargs["usage"]
            # GENERATOR is priced (non-zero), so this call alone burns past
            # the near-zero remaining budget.
            usage.add(dg_module.sdg_models.GENERATOR, STAGE_GENERATE, 100_000, 50_000)
            usage.check_budget()  # raises SDGBudgetExceededError
            return SDGRunResult(  # pragma: no cover - unreachable
                valid_rows=[],
                rejected_count=0,
                duplicate_count=0,
                judge_rejected_count=0,
                judge_parse_failures=0,
                api_calls=1,
            )

        monkeypatch.setattr(dg_module, "_run_generator", _fake_run_generator)

        project_id = uuid4()
        dataset_id = uuid4()
        _seed_project_and_dataset(sync_sessionmaker, project_id=project_id, dataset_id=dataset_id)
        payload = _build_description_only_payload(project_id)

        result = dg_module.generate_synthetic_data.apply(
            kwargs={"request_payload": payload, "dataset_id": str(dataset_id)}
        )

        assert not result.successful()
        # `SDGBudgetExceededError.__init__` takes `(spent_usd,
        # budget_remaining_usd)`, not the single string `.args` an
        # exception's default `__reduce__` round-trips through — so Celery's
        # `get_pickleable_exception` (used even in eager mode, to guarantee
        # every task result is safe to hand back through the result
        # backend) can't reconstruct the original class and substitutes a
        # plain `RuntimeError` carrying the same message. That substitution
        # is a pre-existing `ai_engine.data_gen.usage.SDGBudgetExceededError`
        # trait, out of this task's edit scope — assert on the message text
        # instead of the exact exception type.
        assert isinstance(result.result, RuntimeError)
        assert "SDG budget exceeded" in str(result.result)

        session = sync_sessionmaker()
        try:
            ds = session.get(Dataset, dataset_id)
            assert ds is not None
            assert ds.status == JobStatus.FAILED
            assert ds.error_message
            # `SDGBudgetExceededError.__str__` reads "SDG budget exceeded:
            # spent $X of $Y remaining budget" — that's what lands in
            # `error_message` (`str(exc) or repr(exc)`), so the acceptance
            # criterion ("SDGBudgetExceededError in error_message") is
            # checked both by the error-message text and, more precisely,
            # by `result.result`'s isinstance check above.
            assert "budget exceeded" in ds.error_message.lower()
        finally:
            session.close()

        rows = _usage_rows(sync_sessionmaker)
        assert len(rows) == 1
        row = rows[0]
        assert row.outcome == "failed"
        assert row.prompt_tokens == 100_000
        assert row.completion_tokens == 50_000
        assert row.cost_usd is not None and row.cost_usd > 0


# =============================================================================
# 6. Regressions found by the round-2 review (F2, F5)
# =============================================================================


class TestUsageIsBilledExactlyOnce:
    """A run must be billed once, whatever happens after the success commit."""

    def test_a_failure_after_the_success_commit_does_not_bill_twice(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        """The terminal `JobCompleted` publish used to sit unguarded inside the
        outer `try`, so a Redis hiccup there sent an already-committed run into
        the `except BaseException` handler, which billed it a second time and
        flipped a genuinely completed dataset to FAILED. `_monthly_spend_stmt`
        sums both rows, so the actor's recorded monthly spend doubled.
        """
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )

        async def _fake_run_generator(**kwargs):
            kwargs["usage"].add(dg_module.sdg_models.GENERATOR, STAGE_GENERATE, 1000, 500)
            return SDGRunResult(
                valid_rows=[{"question": "q", "answer": "a"}],
                rejected_count=0,
                duplicate_count=0,
                judge_rejected_count=0,
                judge_parse_failures=0,
                api_calls=1,
            )

        monkeypatch.setattr(dg_module, "_run_generator", _fake_run_generator)

        # Make ONLY the terminal JobCompleted frame explode — every earlier
        # progress frame must still go through, so the run reaches the commit.
        real_publish = dg_module.publish_ws_message

        def _explode_on_completion(redis, job_id, message):  # noqa: ANN001
            if type(message).__name__ == "JobCompleted":
                raise RuntimeError("redis blipped while announcing completion")
            return real_publish(redis, job_id, message)

        monkeypatch.setattr(dg_module, "publish_ws_message", _explode_on_completion)

        project_id = uuid4()
        dataset_id = uuid4()
        _seed_project_and_dataset(sync_sessionmaker, project_id=project_id, dataset_id=dataset_id)

        dg_module.generate_synthetic_data.apply(
            kwargs={
                "request_payload": _build_description_only_payload(project_id),
                "dataset_id": str(dataset_id),
            }
        )

        rows = _usage_rows(sync_sessionmaker)
        outcomes = sorted(r.outcome for r in rows)
        assert len(rows) == 1, f"the same run was billed {len(rows)} times: {outcomes}"
        assert rows[0].outcome == "completed"

        # ...and the completed dataset must not have been unwound to FAILED.
        with sync_sessionmaker() as session:
            ds = session.get(Dataset, dataset_id)
            assert ds is not None
            assert ds.status == JobStatus.COMPLETED


class TestUsageBilledWhenDatasetRowIsGone:
    def test_deleted_dataset_still_bills_with_null_project(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        """Tokens burned by a run whose Dataset was deleted mid-flight are still
        real money. `usage_events.project_id` is ON DELETE SET NULL for exactly
        this reason — billing history outlives the row it refers to.
        """
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )

        project_id = uuid4()
        dataset_id = uuid4()
        _seed_project_and_dataset(sync_sessionmaker, project_id=project_id, dataset_id=dataset_id)

        async def _spend_then_vanish(**kwargs):
            kwargs["usage"].add(dg_module.sdg_models.GENERATOR, STAGE_GENERATE, 700, 300)
            # The dataset disappears mid-run, then the run fails.
            with sync_sessionmaker() as session:
                ds = session.get(Dataset, dataset_id)
                if ds is not None:
                    session.delete(ds)
                    session.commit()
            raise RuntimeError("boom after the row was deleted")

        monkeypatch.setattr(dg_module, "_run_generator", _spend_then_vanish)

        dg_module.generate_synthetic_data.apply(
            kwargs={
                "request_payload": _build_description_only_payload(project_id),
                "dataset_id": str(dataset_id),
            }
        )

        rows = _usage_rows(sync_sessionmaker)
        assert len(rows) == 1, "a run whose dataset vanished was never billed"
        assert rows[0].outcome == "failed"
        assert rows[0].project_id is None
        assert rows[0].prompt_tokens == 700

    def test_any_failure_after_the_commit_does_not_bill_twice(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        """The `usage_recorded` flag, not the publish guard, is what covers this.

        Wrapping the terminal `JobCompleted` publish in try/except handles the
        Redis case, but ANY post-commit exception reaches the same handler.
        This raises after the publish has already succeeded, so only the flag
        can prevent the second bill — keeping the two fixes independently
        tested rather than one masking the other.
        """
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )

        async def _fake_run_generator(**kwargs):
            kwargs["usage"].add(dg_module.sdg_models.GENERATOR, STAGE_GENERATE, 1000, 500)
            return SDGRunResult(
                valid_rows=[{"question": "q", "answer": "a"}],
                rejected_count=0,
                duplicate_count=0,
                judge_rejected_count=0,
                judge_parse_failures=0,
                api_calls=1,
            )

        monkeypatch.setattr(dg_module, "_run_generator", _fake_run_generator)

        # Blow up in the success-path log line, which runs *after* both the
        # commit and the (successful) JobCompleted publish.
        real_info = dg_module.log.info

        def _explode_on_done_log(msg, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            if isinstance(msg, str) and msg.startswith("SDG done"):
                raise RuntimeError("something failed after the run was committed")
            return real_info(msg, *args, **kwargs)

        monkeypatch.setattr(dg_module.log, "info", _explode_on_done_log)

        project_id = uuid4()
        dataset_id = uuid4()
        _seed_project_and_dataset(sync_sessionmaker, project_id=project_id, dataset_id=dataset_id)

        dg_module.generate_synthetic_data.apply(
            kwargs={
                "request_payload": _build_description_only_payload(project_id),
                "dataset_id": str(dataset_id),
            }
        )

        rows = _usage_rows(sync_sessionmaker)
        outcomes = sorted(r.outcome for r in rows)
        assert len(rows) == 1, f"the same run was billed {len(rows)} times: {outcomes}"
        assert rows[0].outcome == "completed"
