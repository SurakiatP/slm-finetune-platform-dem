"""Unit tests for `GET /api/v1/analytics` — the frontend's `/analytics` page.

Same technique as `tests/unit/test_usage_api.py`: a real `app` driven over
HTTP (`TestClient` + `app.dependency_overrides`) against in-memory
aiosqlite, with the `@compiles(JSONB, "sqlite")` shim `Project`/`Dataset`
need to create their tables at all.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from api.core.auth import CurrentUser, require_user
from api.core.database import get_db
from api.main import app
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.evaluation_run import EvaluationRun
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.models.usage_event import UsageEvent
from api.schemas.enums import DatasetSource, JobStatus, TaskType, TrainingMode


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):
    return "JSON"


USER_A = CurrentUser(id="user-a-sub", email="a@example.com")
USER_B = CurrentUser(id="user-b-sub", email="b@example.com")

_KNOWN_MODEL = "google/gemini-2.5-flash-lite"  # priced in model_pricing.py
_UNKNOWN_MODEL = "some/unpriced-model"  # absent from model_pricing.py -> cost_usd IS NULL

# The default test window: Jan 1 - Jan 5 2026 inclusive (5 days). Every
# fixture row's created_at lands inside it unless a test says otherwise.
_WIN_FROM = date(2026, 1, 1)
_WIN_TO = date(2026, 1, 5)


def _dt(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 1, day, hour, minute, tzinfo=UTC)


@pytest.fixture
def client_and_data(monkeypatch: pytest.MonkeyPatch):
    """Seeds two independent owners (A, B), each with a project, an SDG
    dataset, a training job (+ model artifact), an evaluation run and usage
    events -- plus a seed dataset (must be excluded from sdg counts) and a
    second project for A (to prove `project_id` narrows further than
    owner-scoping alone).
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    state: dict = {"user": USER_A}
    ids: dict = {}

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with maker() as session:
            project_a = Project(
                id=uuid4(), name="p-a", task_type=TaskType.QA, owner_id=USER_A.id
            )
            project_a2 = Project(
                id=uuid4(), name="p-a2", task_type=TaskType.QA, owner_id=USER_A.id
            )
            project_b = Project(
                id=uuid4(), name="p-b", task_type=TaskType.QA, owner_id=USER_B.id
            )
            session.add_all([project_a, project_a2, project_b])
            await session.flush()

            # -- Datasets -----------------------------------------------------
            ds_seed = Dataset(
                id=uuid4(), project_id=project_a.id, owner_id=USER_A.id, name="seed",
                task_type=TaskType.QA, source=DatasetSource.SEED,
                status=JobStatus.COMPLETED, created_at=_dt(2, 10),
            )
            ds_sdg1 = Dataset(
                id=uuid4(), project_id=project_a.id, owner_id=USER_A.id, name="sdg1",
                task_type=TaskType.QA, source=DatasetSource.SDG,
                status=JobStatus.COMPLETED, created_at=_dt(2, 11),
            )
            ds_sdg2 = Dataset(
                id=uuid4(), project_id=project_a.id, owner_id=USER_A.id, name="sdg2",
                task_type=TaskType.QA, source=DatasetSource.SDG,
                status=JobStatus.FAILED, created_at=_dt(3, 9),
            )
            ds_sdg_a2 = Dataset(
                id=uuid4(), project_id=project_a2.id, owner_id=USER_A.id, name="sdg-a2",
                task_type=TaskType.QA, source=DatasetSource.SDG,
                status=JobStatus.COMPLETED, created_at=_dt(2, 13),
            )
            ds_sdg_b = Dataset(
                id=uuid4(), project_id=project_b.id, owner_id=USER_B.id, name="sdg-b",
                task_type=TaskType.QA, source=DatasetSource.SDG,
                status=JobStatus.COMPLETED, created_at=_dt(2, 12),
            )
            # Outside the default test window entirely -- must never surface.
            ds_sdg_old = Dataset(
                id=uuid4(), project_id=project_a.id, owner_id=USER_A.id, name="sdg-old",
                task_type=TaskType.QA, source=DatasetSource.SDG,
                status=JobStatus.COMPLETED, created_at=datetime(2025, 12, 15, tzinfo=UTC),
            )
            session.add_all([ds_seed, ds_sdg1, ds_sdg2, ds_sdg_a2, ds_sdg_b, ds_sdg_old])
            await session.flush()

            # -- Training jobs --------------------------------------------------
            tj1 = TrainingJob(
                id=uuid4(), project_id=project_a.id, owner_id=USER_A.id,
                dataset_id=ds_sdg1.id, mode=TrainingMode.MANUAL, base_model="m",
                config_json={}, status=JobStatus.COMPLETED, created_at=_dt(2, 8, 0),
                started_at=_dt(2, 8, 5), ended_at=_dt(2, 8, 15),  # queue=300s, duration=600s
            )
            tj2 = TrainingJob(
                id=uuid4(), project_id=project_a.id, owner_id=USER_A.id,
                dataset_id=ds_sdg2.id, mode=TrainingMode.MANUAL, base_model="m",
                config_json={}, status=JobStatus.PENDING, created_at=_dt(3, 8, 0),
            )
            tj_b = TrainingJob(
                id=uuid4(), project_id=project_b.id, owner_id=USER_B.id,
                dataset_id=ds_sdg_b.id, mode=TrainingMode.MANUAL, base_model="m",
                config_json={}, status=JobStatus.COMPLETED, created_at=_dt(2, 9, 0),
            )
            session.add_all([tj1, tj2, tj_b])
            await session.flush()

            # -- Model artifacts + evaluation runs -------------------------------
            ma1 = ModelArtifact(id=uuid4(), training_job_id=tj1.id, name="ma1", base_model="m")
            ma_b = ModelArtifact(id=uuid4(), training_job_id=tj_b.id, name="ma-b", base_model="m")
            session.add_all([ma1, ma_b])
            await session.flush()

            ev1 = EvaluationRun(
                id=uuid4(), model_artifact_id=ma1.id, dataset_id=ds_sdg1.id,
                status=JobStatus.COMPLETED, created_at=_dt(3, 10, 0),
                started_at=_dt(3, 10, 1), ended_at=_dt(3, 10, 5),  # queue=60s, duration=240s
            )
            ev_b = EvaluationRun(
                id=uuid4(), model_artifact_id=ma_b.id, dataset_id=ds_sdg_b.id,
                status=JobStatus.COMPLETED, created_at=_dt(2, 9, 30),
            )
            session.add_all([ev1, ev_b])

            # -- Usage events ------------------------------------------------
            ue_priced = UsageEvent(
                id=uuid4(), actor_id=USER_A.id, project_id=project_a.id, job_id=None,
                provider="openrouter", model=_KNOWN_MODEL, stage="sdg",
                prompt_tokens=100, completion_tokens=50, cost_usd=Decimal("1.500000"),
                outcome="completed", created_at=_dt(2, 12, 0),
            )
            ue_unpriced = UsageEvent(
                id=uuid4(), actor_id=USER_A.id, project_id=project_a.id, job_id=None,
                provider="openrouter", model=_UNKNOWN_MODEL, stage="sdg",
                prompt_tokens=10, completion_tokens=5, cost_usd=None,
                outcome="completed", created_at=_dt(3, 12, 0),
            )
            ue_b = UsageEvent(
                id=uuid4(), actor_id=USER_B.id, project_id=project_b.id, job_id=None,
                provider="openrouter", model=_KNOWN_MODEL, stage="sdg",
                prompt_tokens=20, completion_tokens=10, cost_usd=Decimal("9.000000"),
                outcome="completed", created_at=_dt(2, 12, 0),
            )
            session.add_all([ue_priced, ue_unpriced, ue_b])
            await session.commit()

            return {
                "project_a": project_a.id,
                "project_a2": project_a2.id,
                "project_b": project_b.id,
            }

    result = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_setup())
    ids.update(result)

    async def _get_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[require_user] = lambda: state["user"]
    with TestClient(app) as client:
        yield client, ids, state
    app.dependency_overrides.clear()


def _get(client, **params):
    params.setdefault("from", _WIN_FROM.isoformat())
    params.setdefault("to", _WIN_TO.isoformat())
    return client.get("/api/v1/analytics", params=params)


def _stage(body: dict, name: str) -> dict:
    return next(s for s in body["stages"] if s["stage"] == name)


class TestOwnerIsolation:
    def test_user_a_never_sees_user_bs_jobs_or_cost(self, client_and_data) -> None:
        client, _ids, _state = client_and_data
        resp = _get(client)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # sdg: sdg1 + sdg2 + sdg-a2 (seed excluded, B's row excluded, the
        # 2025 row is out of window).
        assert _stage(body, "sdg")["total"] == 3
        assert _stage(body, "training")["total"] == 2
        assert _stage(body, "evaluation")["total"] == 1
        assert body["totals"]["jobs_total"] == 6
        assert Decimal(body["totals"]["cost_usd"]) == Decimal("1.500000")
        assert body["totals"]["prompt_tokens"] == 110
        assert body["totals"]["completion_tokens"] == 55

    def test_user_b_sees_only_their_own(self, client_and_data) -> None:
        client, _ids, state = client_and_data
        state["user"] = USER_B
        resp = _get(client)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert _stage(body, "sdg")["total"] == 1
        assert _stage(body, "training")["total"] == 1
        assert _stage(body, "evaluation")["total"] == 1
        assert Decimal(body["totals"]["cost_usd"]) == Decimal("9.000000")


class TestProjectFilter:
    def test_narrows_to_one_project(self, client_and_data) -> None:
        client, ids, _state = client_and_data
        resp = _get(client, project_id=str(ids["project_a"]))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["project_id"] == str(ids["project_a"])
        # Excludes ds_sdg_a2, which lives in project_a2.
        assert _stage(body, "sdg")["total"] == 2

        resp2 = _get(client, project_id=str(ids["project_a2"]))
        body2 = resp2.json()
        assert _stage(body2, "sdg")["total"] == 1
        assert _stage(body2, "training")["total"] == 0
        assert _stage(body2, "evaluation")["total"] == 0

    def test_other_users_project_is_403(self, client_and_data) -> None:
        client, ids, _state = client_and_data
        resp = _get(client, project_id=str(ids["project_b"]))
        assert resp.status_code == 403

    def test_missing_project_is_404(self, client_and_data) -> None:
        client, _ids, _state = client_and_data
        resp = _get(client, project_id=str(uuid4()))
        assert resp.status_code == 404


class TestWindowValidation:
    def test_from_after_to_is_422(self, client_and_data) -> None:
        client, _ids, _state = client_and_data
        resp = _get(client, **{"from": "2026-01-05", "to": "2026-01-01"})
        assert resp.status_code == 422

    def test_span_over_366_days_is_422(self, client_and_data) -> None:
        client, _ids, _state = client_and_data
        resp = _get(client, **{"from": "2020-01-01", "to": "2021-01-02"})
        assert resp.status_code == 422

    def test_span_of_exactly_366_days_is_ok(self, client_and_data) -> None:
        client, _ids, _state = client_and_data
        resp = _get(client, **{"from": "2020-01-01", "to": "2021-01-01"})
        assert resp.status_code == 200

    def test_series_is_zero_filled_for_every_day_in_window(self, client_and_data) -> None:
        client, _ids, _state = client_and_data
        body = _get(client).json()
        expected_days = (_WIN_TO - _WIN_FROM).days + 1
        assert len(body["series"]) == expected_days
        assert [p["date"] for p in body["series"]] == [
            (_WIN_FROM + timedelta(days=i)).isoformat() for i in range(expected_days)
        ]
        # A day with zero jobs is still present, all-zero, not omitted.
        empty_day = next(p for p in body["series"] if p["date"] == "2026-01-01")
        assert empty_day == {
            "date": "2026-01-01", "sdg": 0, "training": 0, "evaluation": 0,
            "completed": 0, "failed": 0, "cost_usd": None,
        }


class TestByStatusZeroFill:
    def test_every_job_status_key_is_present(self, client_and_data) -> None:
        client, _ids, _state = client_and_data
        body = _get(client).json()
        expected_keys = {s.value for s in JobStatus}
        assert set(body["totals"]["by_status"].keys()) == expected_keys
        for stage in body["stages"]:
            assert set(stage["by_status"].keys()) == expected_keys
        # sdg1(completed) + sdg2(failed) + sdg-a2(completed)
        assert _stage(body, "sdg")["by_status"]["completed"] == 2
        assert _stage(body, "sdg")["by_status"]["failed"] == 1
        assert _stage(body, "sdg")["by_status"]["pending"] == 0


class TestCostNullVsUnpriced:
    def test_cost_null_and_flag_set_when_only_unpriced_usage_in_window(
        self, client_and_data
    ) -> None:
        client, _ids, _state = client_and_data
        # Jan 3 only has the unpriced usage event for user A.
        body = _get(client, **{"from": "2026-01-03", "to": "2026-01-03"}).json()
        assert body["totals"]["cost_usd"] is None
        assert body["totals"]["has_unpriced_usage"] is True
        assert body["totals"]["prompt_tokens"] == 10

    def test_cost_null_and_flag_clear_when_no_usage_rows_at_all(
        self, client_and_data
    ) -> None:
        client, ids, _state = client_and_data
        body = _get(client, project_id=str(ids["project_a2"])).json()
        assert body["totals"]["cost_usd"] is None
        assert body["totals"]["has_unpriced_usage"] is False
        assert body["totals"]["prompt_tokens"] == 0


class TestSeedExclusion:
    def test_seed_datasets_are_excluded_from_sdg_counts(self, client_and_data) -> None:
        client, ids, _state = client_and_data
        body = _get(client, project_id=str(ids["project_a"])).json()
        # Only sdg1 + sdg2 -- ds_seed (source=SEED) must not be counted.
        assert _stage(body, "sdg")["total"] == 2


class TestTimings:
    def test_avg_duration_and_queue_wait_are_computed(self, client_and_data) -> None:
        client, _ids, _state = client_and_data
        body = _get(client).json()

        training = _stage(body, "training")
        # Only tj1 has started_at/ended_at; tj2 (pending) contributes nothing.
        assert training["avg_duration_seconds"] == pytest.approx(600.0)
        assert training["avg_queue_wait_seconds"] == pytest.approx(300.0)

        evaluation = _stage(body, "evaluation")
        assert evaluation["avg_duration_seconds"] == pytest.approx(240.0)
        assert evaluation["avg_queue_wait_seconds"] == pytest.approx(60.0)

        sdg = _stage(body, "sdg")
        assert sdg["avg_duration_seconds"] is None
        assert sdg["avg_queue_wait_seconds"] is None
