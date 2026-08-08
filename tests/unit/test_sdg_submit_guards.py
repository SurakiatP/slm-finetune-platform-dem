"""T14 — `submit_sdg_job`'s pre-write submit gate: breaker + budget + quota.

`api/services/sdg_service.py` runs three gates, in order, after project/seed
validation and immediately before the placeholder `Dataset` row is written:

  1. `circuit_breaker.assert_closed()`               -> 503 (+ Retry-After)
  2. `usage_service.assert_within_budget(...)`        -> 402
  3. `quota.assert_can_submit(bucket=Bucket.SDG, ...)` -> 429 (+ Retry-After)

The load-bearing property under test is not just "the right status code
comes back" — it's that a rejection from any one of the three gates leaves
**no trace**: no `Dataset` row inserted, no Celery `apply_async` call. A
gate that ran after the insert (or a `db.flush()` before all three had
passed) would let a rejected submit's own placeholder row count toward the
very quota it was just rejected on.

Everything here runs against an in-memory aiosqlite engine — no Postgres,
no Redis, no Celery broker. Each of `circuit_breaker.assert_closed`,
`usage_service.assert_within_budget`, and `quota.assert_can_submit` is
monkeypatched directly on the module object `sdg_service` imported (not
via `from X import Y`, so patching the module's attribute reaches the
call site) — this isolates the gate-sequencing behavior in `sdg_service`
from the internals of the three gates themselves, which belong to other
modules/tests.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from api.core import request_context
from api.models.audit_event import AuditEvent
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.project import Project
from api.schemas.enums import JobStatus, TaskType
from api.schemas.sdg import SDGRequestDescriptionOnly
from api.services import sdg_service


# ---- KNOWN GOTCHA shim: JSONB is Postgres-only, sqlite needs a stand-in ----
@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


ACTOR_ID = "user-guard-test"


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def project(db: AsyncSession) -> Project:
    proj = Project(id=uuid4(), name="guard-test-proj", task_type=TaskType.QA)
    db.add(proj)
    await db.flush()
    return proj


@pytest.fixture(autouse=True)
def _bind_actor(monkeypatch: pytest.MonkeyPatch):
    """Every gate is keyed on `request_context.current_user_id()`; bind it
    for the duration of each test the same way request-handling middleware
    would."""
    request_context.set_user_id(ACTOR_ID)
    yield
    request_context.set_user_id(None)


@pytest.fixture
def apply_async_spy(monkeypatch: pytest.MonkeyPatch):
    """Patch the exact use-site `sdg_service` calls
    (`generate_synthetic_data.apply_async`) and record whether it fired —
    the acceptance criterion requires proving it did NOT fire on a
    rejected submit, not just checking the status code."""
    import workers.tasks.data_generation as dg_module

    calls: list[dict] = []
    fake_result = type("FakeAsyncResult", (), {"id": "fake-job-id"})()

    def _fake_apply_async(**kwargs):
        calls.append(kwargs)
        return fake_result

    monkeypatch.setattr(dg_module.generate_synthetic_data, "apply_async", _fake_apply_async)
    return calls


def _make_request(project_id) -> SDGRequestDescriptionOnly:
    return SDGRequestDescriptionOnly(
        project_id=project_id,
        task_type=TaskType.QA,
        task_description="Answer questions about our 30-day return policy",
        num_samples=10,
        holdout_size=0,
    )


async def _noop_breaker() -> None:
    return None


async def _noop_budget(db: AsyncSession, *, actor_id: str | None) -> None:
    return None


async def _noop_quota(db: AsyncSession, *, bucket, actor_id: str | None) -> None:
    return None


def _patch_all_gates_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default every gate to pass; individual tests override exactly one."""
    monkeypatch.setattr(sdg_service.circuit_breaker, "assert_closed", _noop_breaker)
    monkeypatch.setattr(sdg_service.usage_service, "assert_within_budget", _noop_budget)
    monkeypatch.setattr(sdg_service.quota, "assert_can_submit", _noop_quota)


async def _dataset_count(db: AsyncSession) -> int:
    return int((await db.execute(select(func.count()).select_from(Dataset))).scalar_one())


async def _audit_count(db: AsyncSession) -> int:
    return int((await db.execute(select(func.count()).select_from(AuditEvent))).scalar_one())


# =============================================================================
# 1. Each gate independently blocks — no Dataset row, no Celery call
# =============================================================================


class TestEachGateBlocksIndependently:
    async def test_open_breaker_returns_503_and_writes_nothing(
        self, db: AsyncSession, project: Project, apply_async_spy, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_all_gates_ok(monkeypatch)

        async def _tripped_breaker() -> None:
            raise HTTPException(
                status_code=503,
                detail="OpenRouter is temporarily unavailable; try again shortly.",
                headers={"Retry-After": "42"},
            )

        monkeypatch.setattr(sdg_service.circuit_breaker, "assert_closed", _tripped_breaker)

        with pytest.raises(HTTPException) as excinfo:
            await sdg_service.submit_sdg_job(db, _make_request(project.id))

        assert excinfo.value.status_code == 503
        assert excinfo.value.headers.get("Retry-After") == "42"
        assert await _dataset_count(db) == 0
        assert await _audit_count(db) == 0
        assert apply_async_spy == []

    async def test_over_budget_returns_402_and_writes_nothing(
        self, db: AsyncSession, project: Project, apply_async_spy, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_all_gates_ok(monkeypatch)

        async def _blown_budget(db: AsyncSession, *, actor_id: str | None) -> None:
            raise HTTPException(status_code=402, detail="Monthly budget exceeded")

        monkeypatch.setattr(sdg_service.usage_service, "assert_within_budget", _blown_budget)

        with pytest.raises(HTTPException) as excinfo:
            await sdg_service.submit_sdg_job(db, _make_request(project.id))

        assert excinfo.value.status_code == 402
        assert await _dataset_count(db) == 0
        assert await _audit_count(db) == 0
        assert apply_async_spy == []

    async def test_quota_exhausted_returns_429_and_writes_nothing(
        self, db: AsyncSession, project: Project, apply_async_spy, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_all_gates_ok(monkeypatch)

        async def _no_quota_left(db: AsyncSession, *, bucket, actor_id: str | None) -> None:
            raise HTTPException(
                status_code=429,
                detail="Your sdg job quota reached (2/2 in flight). Try again shortly.",
                headers={"Retry-After": "30"},
            )

        monkeypatch.setattr(sdg_service.quota, "assert_can_submit", _no_quota_left)

        with pytest.raises(HTTPException) as excinfo:
            await sdg_service.submit_sdg_job(db, _make_request(project.id))

        assert excinfo.value.status_code == 429
        assert excinfo.value.headers.get("Retry-After") == "30"
        assert await _dataset_count(db) == 0
        assert await _audit_count(db) == 0
        assert apply_async_spy == []


# =============================================================================
# 2. All gates satisfied — happy path is unchanged
# =============================================================================


class TestHappyPathUnchanged:
    async def test_all_gates_pass_creates_dataset_and_enqueues_job(
        self, db: AsyncSession, project: Project, apply_async_spy, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_all_gates_ok(monkeypatch)

        response = await sdg_service.submit_sdg_job(db, _make_request(project.id))

        # Same 202 response shape as before this change.
        assert response.job_id == "fake-job-id"
        assert response.status is JobStatus.PENDING
        assert response.websocket_url == f"/ws/jobs/{response.job_id}"

        # Placeholder row created.
        got = await db.get(Dataset, response.dataset_id)
        assert got is not None
        assert got.status == JobStatus.PENDING
        assert got.celery_task_id == "fake-job-id"

        # Celery task enqueued exactly once.
        assert len(apply_async_spy) == 1
        assert apply_async_spy[0]["kwargs"]["dataset_id"] == str(response.dataset_id)

        # Audit row written in the same transaction.
        assert await _audit_count(db) == 1

    async def test_gates_are_called_with_the_current_actor_id(
        self, db: AsyncSession, project: Project, apply_async_spy, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Confirms all three gates are actually wired with `actor_id`, not
        just present — a gate called with the wrong actor would silently
        scope quota/budget to the wrong caller."""
        seen: dict[str, object] = {}

        async def _capture_breaker() -> None:
            seen["breaker"] = True

        async def _capture_budget(db: AsyncSession, *, actor_id: str | None) -> None:
            seen["budget_actor"] = actor_id

        async def _capture_quota(db: AsyncSession, *, bucket, actor_id: str | None) -> None:
            seen["quota_actor"] = actor_id
            seen["quota_bucket"] = bucket

        monkeypatch.setattr(sdg_service.circuit_breaker, "assert_closed", _capture_breaker)
        monkeypatch.setattr(sdg_service.usage_service, "assert_within_budget", _capture_budget)
        monkeypatch.setattr(sdg_service.quota, "assert_can_submit", _capture_quota)

        await sdg_service.submit_sdg_job(db, _make_request(project.id))

        assert seen["breaker"] is True
        assert seen["budget_actor"] == ACTOR_ID
        assert seen["quota_actor"] == ACTOR_ID
        assert seen["quota_bucket"] is sdg_service.Bucket.SDG
