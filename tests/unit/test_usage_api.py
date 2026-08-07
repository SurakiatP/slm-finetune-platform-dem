"""Unit tests for T13 — the usage read API.

Drives `GET /api/v1/projects/{id}/usage` and `GET /api/v1/usage` over real
HTTP (`TestClient` + `app.dependency_overrides`), the same technique
`TestActivityEndpointOverHttp` in `tests/unit/test_verification_pass.py`
uses for `/activity` — this router is explicitly modelled on that one.

Runs entirely against in-memory aiosqlite. `Base.metadata.create_all`
pulls in every mapped table, including `audit_events` (JSONB), so the
`@compiles(JSONB, "sqlite")` shim from `tests/unit/test_usage_service.py`
/ `test_audit_events.py` is mirrored here even though these tests never
touch that table themselves.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
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
from api.models.project import Project
from api.models.usage_event import UsageEvent
from api.schemas.enums import TaskType


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


USER_A = CurrentUser(id="user-a-sub", email="a@example.com")
USER_B = CurrentUser(id="user-b-sub", email="b@example.com")

_KNOWN_MODEL = "google/gemini-2.5-flash-lite"  # priced in model_pricing.py


def _event(
    *,
    actor_id: str | None,
    project_id,
    model: str = _KNOWN_MODEL,
    stage: str = "sdg",
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    cost_usd: Decimal | None = Decimal("1.000000"),
    created_at: datetime,
) -> UsageEvent:
    return UsageEvent(
        id=uuid4(),
        actor_id=actor_id,
        project_id=project_id,
        job_id=None,
        provider="openrouter",
        model=model,
        stage=stage,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd,
        outcome="completed",
        created_at=created_at,
    )


@pytest.fixture
def client_and_data(monkeypatch: pytest.MonkeyPatch):
    """Boots a real `app` against an in-memory DB seeded with:

      - project P, owned by USER_A
      - 2 usage rows on P actor'd to USER_A (this calendar month)
      - 1 usage row actor'd to USER_B, on a *different* project (also this
        month) — the cross-actor isolation case for `/api/v1/usage`
      - 1 usage row actor'd to USER_A from last calendar month — must not
        appear in the `/api/v1/usage` rollup (it's scoped to "this month")
        but still shows up in the project-scoped `/usage` log, which has
        no date bound
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    state: dict = {"user": USER_A}

    now = datetime.now(timezone.utc)
    this_month = now.replace(day=1, hour=1, minute=0, second=0, microsecond=0)
    last_month = (this_month - timedelta(days=1)).replace(day=15, hour=1)

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with maker() as session:
            project_a = Project(
                id=uuid4(), name="p-a", task_type=TaskType.QA, owner_id=USER_A.id
            )
            project_b = Project(
                id=uuid4(), name="p-b", task_type=TaskType.QA, owner_id=USER_B.id
            )
            session.add_all([project_a, project_b])
            await session.flush()

            session.add_all(
                [
                    _event(
                        actor_id=USER_A.id,
                        project_id=project_a.id,
                        stage="sdg",
                        created_at=this_month + timedelta(seconds=1),
                    ),
                    _event(
                        actor_id=USER_A.id,
                        project_id=project_a.id,
                        stage="eval",
                        created_at=this_month + timedelta(seconds=2),
                    ),
                    _event(
                        actor_id=USER_A.id,
                        project_id=project_a.id,
                        created_at=last_month,
                    ),
                    _event(
                        actor_id=USER_B.id,
                        project_id=project_b.id,
                        created_at=this_month + timedelta(seconds=3),
                    ),
                ]
            )
            await session.commit()
            return project_a.id, project_b.id

    project_a_id, project_b_id = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        _setup()
    )

    async def _get_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[require_user] = lambda: state["user"]
    with TestClient(app) as client:
        yield client, project_a_id, project_b_id, state
    app.dependency_overrides.clear()


# =============================================================================
# GET /projects/{project_id}/usage
# =============================================================================


class TestProjectUsageEndpoint:
    def test_owner_gets_rows_newest_first_and_paginates(self, client_and_data) -> None:
        client, project_a_id, _project_b_id, _state = client_and_data
        resp = client.get(f"/api/v1/projects/{project_a_id}/usage")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 3  # both this-month rows + the last-month one
        assert [i["stage"] for i in body["items"]] == ["eval", "sdg", "sdg"]

        page = client.get(f"/api/v1/projects/{project_a_id}/usage?limit=1&offset=1")
        assert page.status_code == 200
        assert [i["stage"] for i in page.json()["items"]] == ["sdg"]
        assert page.json()["total"] == 3

    def test_non_owner_gets_403_not_an_empty_page(self, client_and_data) -> None:
        """ADR-012: an existing project owned by someone else is 403, not
        the 404 this used to be — a `200` with `items=[]` would leak the
        same thing the old 404 was designed to avoid."""
        client, project_a_id, _project_b_id, state = client_and_data
        state["user"] = USER_B
        resp = client.get(f"/api/v1/projects/{project_a_id}/usage")
        assert resp.status_code == 403

    def test_missing_project_gets_404(self, client_and_data) -> None:
        """Pair for the test above: a project id that names no row at all
        must stay 404, distinct from the 403 an existing-but-not-yours
        project now gets."""
        client, _project_a_id, _project_b_id, state = client_and_data
        state["user"] = USER_B
        resp = client.get(f"/api/v1/projects/{uuid4()}/usage")
        assert resp.status_code == 404

    def test_owner_of_a_different_project_only_sees_their_own(
        self, client_and_data
    ) -> None:
        client, _project_a_id, project_b_id, state = client_and_data
        state["user"] = USER_B
        resp = client.get(f"/api/v1/projects/{project_b_id}/usage")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["actor_id"] == USER_B.id

    def test_pagination_params_are_validated(self, client_and_data) -> None:
        client, project_a_id, _project_b_id, _state = client_and_data
        assert (
            client.get(f"/api/v1/projects/{project_a_id}/usage?limit=0").status_code
            == 422
        )


# =============================================================================
# GET /api/v1/usage
# =============================================================================


class TestUsageSummaryEndpoint:
    def test_rollup_is_restricted_to_the_callers_own_actor_id(
        self, client_and_data
    ) -> None:
        client, _project_a_id, _project_b_id, state = client_and_data
        resp = client.get("/api/v1/usage")
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Only USER_A's *this-month* rows: 2 events, not the 3rd (last
        # month) and not USER_B's row on a different project.
        assert body["prompt_tokens"] == 200
        assert body["completion_tokens"] == 100
        assert Decimal(body["cost_usd"]) == Decimal("2.000000")
        stages = sorted(item["stage"] for item in body["items"])
        assert stages == ["eval", "sdg"]

        state["user"] = USER_B
        other = client.get("/api/v1/usage")
        assert other.status_code == 200
        other_body = other.json()
        assert other_body["prompt_tokens"] == 100
        assert other_body["completion_tokens"] == 50
        assert [item["stage"] for item in other_body["items"]] == ["sdg"]

    def test_period_start_is_the_beginning_of_the_current_month(
        self, client_and_data
    ) -> None:
        client, _project_a_id, _project_b_id, _state = client_and_data
        body = client.get("/api/v1/usage").json()
        period_start = datetime.fromisoformat(body["period_start"])
        assert period_start.day == 1
        assert period_start.hour == 0 and period_start.minute == 0


# =============================================================================
# Both routes must be ownership-protected per the auth-enforcement guard
# =============================================================================


def test_both_routes_are_in_the_protected_set() -> None:
    from tests.unit.test_auth_enforcement import PROTECTED

    protected_paths = {path for _method, path in PROTECTED}
    assert "/api/v1/projects/{project_id}/usage" in protected_paths
    assert "/api/v1/usage" in protected_paths
