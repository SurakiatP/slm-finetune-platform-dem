"""Unit tests for `api/services/usage_service.py`.

Layers covered:
  1. Write path      — `record()` / `record_run()` only `add()`, resolve
                        `cost_usd` via `model_pricing`, and leave it NULL
                        (never 0) for an unknown model.
  2. Aggregates       — `monthly_spend_usd(_sync)` / `global_monthly_spend_usd(_sync)`
                        ignore rows from a previous calendar month and rows
                        with NULL `cost_usd`.
  3. Budget gate      — `assert_within_budget` (async, raises 402) and
                        `assert_within_budget_sync` (raises `BudgetExceeded`):
                        no-op when both settings are `None`; per-actor cap;
                        global cap applying even to an anonymous caller.
  4. `summary_for_actor` — grouped by (model, stage), `has_unpriced_usage`.
  5. `list_project_usage` — 404s for a non-owner before touching rows.
  6. Import-surface   — must import cleanly with `jwt` blocked at the
                        meta-path, same technique as
                        `tests/unit/test_worker_import_surface.py`, since
                        this module is a worker-boot dependency
                        (`workers/tasks/data_generation.py` calls `record()`
                        / `record_run()`).

Runs entirely against in-memory (a)sqlite — no Postgres, no Docker. Mirrors
the `@compiles(JSONB, "sqlite")` shim from `tests/unit/test_audit_events.py`
for the async fixture (`Base.metadata` includes `audit_events`, which has a
JSONB column); the sync fixture only creates `projects` + `usage_events`
(no JSONB column on either), so it needs no shim, matching
`tests/unit/test_usage_events_model.py`.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from ai_engine.data_gen.usage import UsageEntry
from api.core.auth import CurrentUser
from api.core.config import get_settings
from api.models.base import Base
from api.models.project import Project
from api.models.usage_event import UsageEvent
from api.schemas.enums import TaskType
from api.services import usage_service

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Matches the built-in row in api/services/model_pricing.py: $0.10 prompt /
# $0.40 completion per 1M tokens.
_KNOWN_MODEL = "google/gemini-2.5-flash-lite"
_UNKNOWN_MODEL = "some-vendor/does-not-exist"


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):
    return "JSON"


@pytest.fixture(autouse=True)
def _reset_usage_env(monkeypatch: pytest.MonkeyPatch):
    """Every test starts with no pricing override and unlimited budgets,
    and leaves `get_settings()` clean for whichever test runs next.
    """
    for key in ("MODEL_PRICING_JSON", "BUDGET_MONTHLY_USD_PER_ACTOR", "BUDGET_MONTHLY_USD_GLOBAL"):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
async def async_session():
    """In-memory aiosqlite engine + AsyncSession with the full ORM schema —
    used for every async-path test (`record`, the async aggregates, the
    async budget gate, `summary_for_actor`, `list_project_usage`).
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def sync_sessionmaker_fixture():
    """In-memory sync sqlite engine + sessionmaker, `projects` +
    `usage_events` only — the worker-side (`Session`) counterpart to
    `async_session`, used for the `*_sync` variants.
    """
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine, tables=[Project.__table__, UsageEvent.__table__])
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    yield maker
    engine.dispose()


async def _seed_project(session: AsyncSession, *, owner_id: str | None) -> Project:
    project = Project(id=uuid4(), name="proj", task_type=TaskType.QA, owner_id=owner_id)
    session.add(project)
    await session.flush()
    return project


# =============================================================================
# 1. Write path
# =============================================================================


class TestRecord:
    async def test_unknown_model_writes_null_cost_and_correct_tokens(
        self, async_session: AsyncSession
    ) -> None:
        usage_service.record(
            async_session,
            actor_id="user-1",
            project_id=None,
            job_id="job-1",
            provider="openrouter",
            model=_UNKNOWN_MODEL,
            stage="generate",
            prompt_tokens=123,
            completion_tokens=45,
            outcome="completed",
        )
        await async_session.commit()

        rows = (await async_session.execute(select(UsageEvent))).scalars().all()
        assert len(rows) == 1
        assert rows[0].cost_usd is None
        assert rows[0].prompt_tokens == 123
        assert rows[0].completion_tokens == 45

    async def test_known_model_writes_arithmetically_correct_decimal(
        self, async_session: AsyncSession
    ) -> None:
        usage_service.record(
            async_session,
            actor_id=None,
            project_id=None,
            job_id=None,
            provider="openrouter",
            model=_KNOWN_MODEL,
            stage="generate",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
            outcome="completed",
        )
        await async_session.commit()

        rows = (await async_session.execute(select(UsageEvent))).scalars().all()
        assert rows[0].cost_usd == Decimal("0.500000")

    async def test_only_adds_no_commit_of_its_own(self, async_session: AsyncSession) -> None:
        event = usage_service.record(
            async_session,
            actor_id=None,
            project_id=None,
            job_id=None,
            provider="openrouter",
            model=_KNOWN_MODEL,
            stage="generate",
            prompt_tokens=1,
            completion_tokens=1,
            outcome="completed",
        )
        assert event in async_session.new

        await async_session.rollback()

        rows = (await async_session.execute(select(UsageEvent))).scalars().all()
        assert rows == []


class TestRecordRun:
    async def test_one_row_per_model_stage_pair(self, async_session: AsyncSession) -> None:
        entries = [
            UsageEntry(model=_KNOWN_MODEL, stage="generate", prompt_tokens=10, completion_tokens=5),
            UsageEntry(model=_UNKNOWN_MODEL, stage="judge", prompt_tokens=20, completion_tokens=8),
        ]
        events = usage_service.record_run(
            async_session,
            entries,
            actor_id="user-1",
            project_id=None,
            job_id="job-1",
            outcome="completed",
        )
        await async_session.commit()

        assert len(events) == 2
        rows = (await async_session.execute(select(UsageEvent))).scalars().all()
        assert {r.model for r in rows} == {_KNOWN_MODEL, _UNKNOWN_MODEL}
        assert all(r.provider == "openrouter" for r in rows)
        assert all(r.job_id == "job-1" for r in rows)
        priced = next(r for r in rows if r.model == _KNOWN_MODEL)
        unpriced = next(r for r in rows if r.model == _UNKNOWN_MODEL)
        assert priced.cost_usd is not None
        assert unpriced.cost_usd is None

    async def test_default_provider_is_openrouter(self, async_session: AsyncSession) -> None:
        entries = [UsageEntry(model=_KNOWN_MODEL, stage="generate", prompt_tokens=1, completion_tokens=1)]
        usage_service.record_run(
            async_session, entries, actor_id=None, project_id=None, job_id=None, outcome="completed"
        )
        await async_session.commit()
        rows = (await async_session.execute(select(UsageEvent))).scalars().all()
        assert rows[0].provider == "openrouter"


# =============================================================================
# 2. Aggregates
# =============================================================================


class TestMonthlySpendAsync:
    async def test_ignores_previous_month_and_null_cost_rows(
        self, async_session: AsyncSession
    ) -> None:
        now = datetime.now(UTC)
        this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        prev_month_ts = this_month_start - timedelta(days=1)

        this_month_priced = usage_service.record(
            async_session,
            actor_id="user-1",
            project_id=None,
            job_id=None,
            provider="openrouter",
            model=_KNOWN_MODEL,
            stage="generate",
            prompt_tokens=1_000_000,
            completion_tokens=0,
            outcome="completed",
        )
        this_month_unpriced = usage_service.record(
            async_session,
            actor_id="user-1",
            project_id=None,
            job_id=None,
            provider="openrouter",
            model=_UNKNOWN_MODEL,
            stage="generate",
            prompt_tokens=999,
            completion_tokens=1,
            outcome="completed",
        )
        prev_month_priced = usage_service.record(
            async_session,
            actor_id="user-1",
            project_id=None,
            job_id=None,
            provider="openrouter",
            model=_KNOWN_MODEL,
            stage="generate",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
            outcome="completed",
        )
        this_month_priced.created_at = now
        this_month_unpriced.created_at = now
        prev_month_priced.created_at = prev_month_ts
        await async_session.commit()

        spent = await usage_service.monthly_spend_usd(async_session, actor_id="user-1")
        # Only this_month_priced contributes: 1_000_000 prompt tokens *
        # $0.10/1M = $0.10. The NULL-cost row contributes nothing (that is
        # what makes this a floor), and the previous-month row is excluded
        # by the date filter even though it's the largest cost of the three.
        assert spent == Decimal("0.100000")

    async def test_ignores_other_actors(self, async_session: AsyncSession) -> None:
        mine = usage_service.record(
            async_session,
            actor_id="user-1",
            project_id=None,
            job_id=None,
            provider="openrouter",
            model=_KNOWN_MODEL,
            stage="generate",
            prompt_tokens=1_000_000,
            completion_tokens=0,
            outcome="completed",
        )
        theirs = usage_service.record(
            async_session,
            actor_id="user-2",
            project_id=None,
            job_id=None,
            provider="openrouter",
            model=_KNOWN_MODEL,
            stage="generate",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
            outcome="completed",
        )
        now = datetime.now(UTC)
        mine.created_at = now
        theirs.created_at = now
        await async_session.commit()

        spent = await usage_service.monthly_spend_usd(async_session, actor_id="user-1")
        assert spent == Decimal("0.100000")

    async def test_global_includes_all_actors_current_month_only(
        self, async_session: AsyncSession
    ) -> None:
        now = datetime.now(UTC)
        this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        prev_month_ts = this_month_start - timedelta(days=1)

        user1_row = usage_service.record(
            async_session,
            actor_id="user-1",
            project_id=None,
            job_id=None,
            provider="openrouter",
            model=_KNOWN_MODEL,
            stage="generate",
            prompt_tokens=1_000_000,
            completion_tokens=0,
            outcome="completed",
        )
        anon_row = usage_service.record(
            async_session,
            actor_id=None,
            project_id=None,
            job_id=None,
            provider="openrouter",
            model=_KNOWN_MODEL,
            stage="generate",
            prompt_tokens=0,
            completion_tokens=1_000_000,
            outcome="completed",
        )
        prev_month_row = usage_service.record(
            async_session,
            actor_id="user-2",
            project_id=None,
            job_id=None,
            provider="openrouter",
            model=_KNOWN_MODEL,
            stage="generate",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
            outcome="completed",
        )
        user1_row.created_at = now
        anon_row.created_at = now
        prev_month_row.created_at = prev_month_ts
        await async_session.commit()

        spent = await usage_service.global_monthly_spend_usd(async_session)
        # user1_row: $0.10 (prompt only) + anon_row: $0.40 (completion only) = $0.50.
        # prev_month_row excluded by date.
        assert spent == Decimal("0.500000")

    async def test_zero_when_no_rows(self, async_session: AsyncSession) -> None:
        assert await usage_service.monthly_spend_usd(async_session, actor_id="nobody") == Decimal("0")
        assert await usage_service.global_monthly_spend_usd(async_session) == Decimal("0")


class TestMonthlySpendSync:
    def test_sync_variants_match_the_same_aggregate(
        self, sync_sessionmaker_fixture: sessionmaker
    ) -> None:
        session = sync_sessionmaker_fixture()
        try:
            now = datetime.now(UTC)
            event = usage_service.record(
                session,
                actor_id="user-1",
                project_id=None,
                job_id=None,
                provider="openrouter",
                model=_KNOWN_MODEL,
                stage="generate",
                prompt_tokens=1_000_000,
                completion_tokens=0,
                outcome="completed",
            )
            event.created_at = now
            session.commit()

            assert usage_service.monthly_spend_usd_sync(session, actor_id="user-1") == Decimal(
                "0.100000"
            )
            assert usage_service.global_monthly_spend_usd_sync(session) == Decimal("0.100000")
            # A different actor sees none of it.
            assert usage_service.monthly_spend_usd_sync(session, actor_id="someone-else") == Decimal(
                "0"
            )
        finally:
            session.close()

    def test_sync_ignores_previous_month(self, sync_sessionmaker_fixture: sessionmaker) -> None:
        session = sync_sessionmaker_fixture()
        try:
            now = datetime.now(UTC)
            this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            prev_month_ts = this_month_start - timedelta(days=1)

            event = usage_service.record(
                session,
                actor_id="user-1",
                project_id=None,
                job_id=None,
                provider="openrouter",
                model=_KNOWN_MODEL,
                stage="generate",
                prompt_tokens=1_000_000,
                completion_tokens=1_000_000,
                outcome="completed",
            )
            event.created_at = prev_month_ts
            session.commit()

            assert usage_service.monthly_spend_usd_sync(session, actor_id="user-1") == Decimal("0")
            assert usage_service.global_monthly_spend_usd_sync(session) == Decimal("0")
        finally:
            session.close()


# =============================================================================
# 3. Budget gate
# =============================================================================


class TestAssertWithinBudgetAsync:
    async def test_noop_when_both_limits_none(self, async_session: AsyncSession) -> None:
        # Defaults are unlimited; must not raise even with no rows at all.
        await usage_service.assert_within_budget(async_session, actor_id="user-1")
        await usage_service.assert_within_budget(async_session, actor_id=None)

    async def test_raises_402_at_per_actor_cap(
        self, async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        usage_service.record(
            async_session,
            actor_id="user-1",
            project_id=None,
            job_id=None,
            provider="openrouter",
            model=_KNOWN_MODEL,
            stage="generate",
            prompt_tokens=1_000_000,
            completion_tokens=0,
            outcome="completed",
        )
        await async_session.commit()  # created_at defaults to "now" — this month

        monkeypatch.setenv("BUDGET_MONTHLY_USD_PER_ACTOR", "0.10")
        get_settings.cache_clear()

        with pytest.raises(HTTPException) as excinfo:
            await usage_service.assert_within_budget(async_session, actor_id="user-1")
        assert excinfo.value.status_code == 402

    async def test_per_actor_cap_not_checked_for_other_actors(
        self, async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        usage_service.record(
            async_session,
            actor_id="user-1",
            project_id=None,
            job_id=None,
            provider="openrouter",
            model=_KNOWN_MODEL,
            stage="generate",
            prompt_tokens=1_000_000,
            completion_tokens=0,
            outcome="completed",
        )
        await async_session.commit()

        monkeypatch.setenv("BUDGET_MONTHLY_USD_PER_ACTOR", "0.10")
        get_settings.cache_clear()

        # user-2 has no usage this month, so is nowhere near the cap.
        await usage_service.assert_within_budget(async_session, actor_id="user-2")

    async def test_global_cap_applies_to_anonymous_caller(
        self, async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        usage_service.record(
            async_session,
            actor_id=None,
            project_id=None,
            job_id=None,
            provider="openrouter",
            model=_KNOWN_MODEL,
            stage="generate",
            prompt_tokens=1_000_000,
            completion_tokens=0,
            outcome="completed",
        )
        await async_session.commit()

        monkeypatch.setenv("BUDGET_MONTHLY_USD_GLOBAL", "0.10")
        get_settings.cache_clear()

        with pytest.raises(HTTPException) as excinfo:
            await usage_service.assert_within_budget(async_session, actor_id=None)
        assert excinfo.value.status_code == 402

    async def test_per_actor_cap_skipped_when_actor_id_is_none(
        self, async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Per-actor cap set (and would immediately trip for any real actor),
        # but there's no actor to check it against and the global cap is
        # unset — an anonymous caller must sail through.
        monkeypatch.setenv("BUDGET_MONTHLY_USD_PER_ACTOR", "0.00")
        get_settings.cache_clear()

        await usage_service.assert_within_budget(async_session, actor_id=None)


class TestAssertWithinBudgetSync:
    def test_noop_when_both_limits_none(self, sync_sessionmaker_fixture: sessionmaker) -> None:
        session = sync_sessionmaker_fixture()
        try:
            usage_service.assert_within_budget_sync(session, actor_id="user-1")
            usage_service.assert_within_budget_sync(session, actor_id=None)
        finally:
            session.close()

    def test_raises_budget_exceeded_at_per_actor_cap(
        self, sync_sessionmaker_fixture: sessionmaker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = sync_sessionmaker_fixture()
        try:
            usage_service.record(
                session,
                actor_id="user-1",
                project_id=None,
                job_id=None,
                provider="openrouter",
                model=_KNOWN_MODEL,
                stage="generate",
                prompt_tokens=1_000_000,
                completion_tokens=0,
                outcome="completed",
            )
            session.commit()

            monkeypatch.setenv("BUDGET_MONTHLY_USD_PER_ACTOR", "0.10")
            get_settings.cache_clear()

            with pytest.raises(usage_service.BudgetExceeded):
                usage_service.assert_within_budget_sync(session, actor_id="user-1")
        finally:
            session.close()

    def test_global_cap_raises_for_anonymous_actor(
        self, sync_sessionmaker_fixture: sessionmaker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = sync_sessionmaker_fixture()
        try:
            usage_service.record(
                session,
                actor_id=None,
                project_id=None,
                job_id=None,
                provider="openrouter",
                model=_KNOWN_MODEL,
                stage="generate",
                prompt_tokens=1_000_000,
                completion_tokens=0,
                outcome="completed",
            )
            session.commit()

            monkeypatch.setenv("BUDGET_MONTHLY_USD_GLOBAL", "0.10")
            get_settings.cache_clear()

            with pytest.raises(usage_service.BudgetExceeded):
                usage_service.assert_within_budget_sync(session, actor_id=None)
        finally:
            session.close()


# =============================================================================
# 4. summary_for_actor
# =============================================================================


class TestSummaryForActor:
    async def test_groups_by_model_and_stage_and_flags_unpriced(
        self, async_session: AsyncSession
    ) -> None:
        since = datetime(2026, 1, 1, tzinfo=UTC)
        within = datetime(2026, 1, 15, tzinfo=UTC)

        e1 = usage_service.record(
            async_session,
            actor_id="user-1",
            project_id=None,
            job_id=None,
            provider="openrouter",
            model=_KNOWN_MODEL,
            stage="generate",
            prompt_tokens=1_000_000,
            completion_tokens=0,
            outcome="completed",
        )
        e2 = usage_service.record(
            async_session,
            actor_id="user-1",
            project_id=None,
            job_id=None,
            provider="openrouter",
            model=_KNOWN_MODEL,
            stage="generate",
            prompt_tokens=1_000_000,
            completion_tokens=0,
            outcome="completed",
        )
        e3 = usage_service.record(
            async_session,
            actor_id="user-1",
            project_id=None,
            job_id=None,
            provider="openrouter",
            model=_UNKNOWN_MODEL,
            stage="judge",
            prompt_tokens=50,
            completion_tokens=50,
            outcome="completed",
        )
        e4_other_actor = usage_service.record(
            async_session,
            actor_id="user-2",
            project_id=None,
            job_id=None,
            provider="openrouter",
            model=_KNOWN_MODEL,
            stage="generate",
            prompt_tokens=1_000_000,
            completion_tokens=0,
            outcome="completed",
        )
        for event in (e1, e2, e3, e4_other_actor):
            event.created_at = within
        await async_session.commit()

        summary = await usage_service.summary_for_actor(
            async_session, actor_id="user-1", since=since
        )

        assert summary.has_unpriced_usage is True
        assert summary.prompt_tokens == 1_000_000 * 2 + 50
        assert summary.completion_tokens == 50
        assert summary.cost_usd == Decimal("0.200000")

        by_key = {(item.model, item.stage): item for item in summary.items}
        assert set(by_key) == {(_KNOWN_MODEL, "generate"), (_UNKNOWN_MODEL, "judge")}
        assert by_key[(_KNOWN_MODEL, "generate")].cost_usd == Decimal("0.200000")
        assert by_key[(_KNOWN_MODEL, "generate")].prompt_tokens == 2_000_000
        assert by_key[(_UNKNOWN_MODEL, "judge")].cost_usd is None

    async def test_since_boundary_excludes_older_rows(self, async_session: AsyncSession) -> None:
        since = datetime(2026, 2, 1, tzinfo=UTC)
        older = usage_service.record(
            async_session,
            actor_id="user-1",
            project_id=None,
            job_id=None,
            provider="openrouter",
            model=_KNOWN_MODEL,
            stage="generate",
            prompt_tokens=1_000_000,
            completion_tokens=0,
            outcome="completed",
        )
        older.created_at = datetime(2026, 1, 15, tzinfo=UTC)
        await async_session.commit()

        summary = await usage_service.summary_for_actor(
            async_session, actor_id="user-1", since=since
        )
        assert summary.items == []
        assert summary.cost_usd is None
        assert summary.has_unpriced_usage is False
        assert summary.prompt_tokens == 0

    async def test_no_unpriced_rows_flag_is_false(self, async_session: AsyncSession) -> None:
        since = datetime(2026, 1, 1, tzinfo=UTC)
        event = usage_service.record(
            async_session,
            actor_id="user-1",
            project_id=None,
            job_id=None,
            provider="openrouter",
            model=_KNOWN_MODEL,
            stage="generate",
            prompt_tokens=1,
            completion_tokens=1,
            outcome="completed",
        )
        event.created_at = datetime(2026, 1, 15, tzinfo=UTC)
        await async_session.commit()

        summary = await usage_service.summary_for_actor(
            async_session, actor_id="user-1", since=since
        )
        assert summary.has_unpriced_usage is False


# =============================================================================
# 5. list_project_usage
# =============================================================================


class TestListProjectUsage:
    async def test_404_for_non_owner(self, async_session: AsyncSession) -> None:
        project = await _seed_project(async_session, owner_id="user-1")
        await async_session.commit()

        other_user = CurrentUser(id="user-2", email=None)
        with pytest.raises(HTTPException) as excinfo:
            await usage_service.list_project_usage(
                async_session, project.id, limit=10, offset=0, user=other_user
            )
        assert excinfo.value.status_code == 404

    async def test_owner_gets_rows_newest_first(self, async_session: AsyncSession) -> None:
        project = await _seed_project(async_session, owner_id="user-1")
        await async_session.commit()

        base = datetime(2026, 1, 1, tzinfo=UTC)
        for i in range(3):
            event = usage_service.record(
                async_session,
                actor_id="user-1",
                project_id=project.id,
                job_id=f"job-{i}",
                provider="openrouter",
                model=_KNOWN_MODEL,
                stage="generate",
                prompt_tokens=1,
                completion_tokens=1,
                outcome="completed",
            )
            event.created_at = base + timedelta(seconds=i)
        await async_session.commit()

        user = CurrentUser(id="user-1", email=None)
        page = await usage_service.list_project_usage(
            async_session, project.id, limit=10, offset=0, user=user
        )
        assert page.total == 3
        assert [item.job_id for item in page.items] == ["job-2", "job-1", "job-0"]

    async def test_none_user_is_noop_bypass(self, async_session: AsyncSession) -> None:
        project = await _seed_project(async_session, owner_id="user-1")
        await async_session.commit()

        usage_service.record(
            async_session,
            actor_id=None,
            project_id=project.id,
            job_id=None,
            provider="openrouter",
            model=_KNOWN_MODEL,
            stage="generate",
            prompt_tokens=1,
            completion_tokens=1,
            outcome="completed",
        )
        await async_session.commit()

        page = await usage_service.list_project_usage(
            async_session, project.id, limit=10, offset=0, user=None
        )
        assert page.total == 1


# =============================================================================
# 6. Import-surface: must not pull in PyJWT.
# =============================================================================

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


def _import_without_pyjwt(module: str) -> subprocess.CompletedProcess[str]:
    import os

    return subprocess.run(
        [sys.executable, "-c", _BLOCK_JWT_AND_IMPORT.format(module=module)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        env={
            **dict(os.environ),
            "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test_unused",
        },
    )


def test_usage_service_is_importable_without_pyjwt() -> None:
    """The specific module the worker calls `record()`/`record_run()` on:
    the write path needs neither a token nor an ownership check."""
    result = _import_without_pyjwt("api.services.usage_service")
    assert "IMPORTED" in result.stdout, result.stderr


def test_the_read_path_still_enforces_ownership() -> None:
    """Deferring the `ownership` import must not quietly drop the access
    check — `list_project_usage`'s first act is still `assert_project_access`."""
    src = (_REPO_ROOT / "api" / "services" / "usage_service.py").read_text(encoding="utf-8")
    body = src.split("async def list_project_usage")[1]
    assert "ownership.assert_project_access" in body
    assert "from api.services import ownership" in body, (
        "the deferred import must live inside list_project_usage, not at module scope"
    )
