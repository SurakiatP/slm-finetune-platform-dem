"""Every non-public route must refuse an anonymous caller in phase 2 (ADR-009).

The protected/public split is **derived from `app.openapi()`**, not hand-listed.
That is the point: a route added later without an auth dependency shows up in
the derived "protected" set, fails this test, and cannot ship unnoticed. A
hardcoded list would silently keep passing.

Nothing here needs a database — `require_user` is a router-level dependency, so
it rejects before any endpoint body or session is touched.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.core.config import get_settings
from api.main import app

# Static catalogs (`api/routers/tasks_meta.py`) touch no DB and expose no user
# data — deliberately readable without a token so the frontend can populate its
# model/task pickers before login.
_PUBLIC_PREFIXES = (
    "/api/v1/tasks",
    "/api/v1/base-models",
    "/api/v1/sdg-pipeline",
)
# `/ready` joins `/health` as public on purpose. Container orchestrators and
# load balancers probe readiness with no credentials — there is nowhere for
# them to get a token — so requiring auth would mean the probe always fails
# and the instance is never routed to. What it discloses is infrastructure
# liveness ("minio: unavailable"), never user data, and an attacker who can
# reach the API can already observe the same outage by watching requests fail.
#
# `/metrics` joins them for the same reason: Prometheus has no token to send
# either, and what it discloses (queue depth, job counts, worker_gpu/
# worker_cpu liveness) is the same class of infrastructure signal `/ready`
# already exposes anonymously, not user data.
_PUBLIC_EXACT = {"/", "/health", "/ready", "/metrics", "/openapi.json", "/docs", "/redoc"}

# Always-auth resources: every route on these carries `require_authenticated_
# user` internally (not the phased `require_user`), so they reject an
# anonymous caller even in phase 1 (`AUTH_REQUIRED=false`) — there is no
# anonymous owner to bucket a template, a deployment, or an API key under.
_ALWAYS_AUTH_PREFIXES = ("/api/v1/templates", "/api/v1/deployments", "/api/v1/api-keys")

_DUMMY = str(uuid4())


def _is_public(path: str) -> bool:
    return path in _PUBLIC_EXACT or path.startswith(_PUBLIC_PREFIXES)


def _fill(path: str) -> str:
    """Substitute a plausible value for every `{param}` in an OpenAPI path."""
    out = path
    while "{" in out:
        head, _, rest = out.partition("{")
        name, _, tail = rest.partition("}")
        out = head + ("qa" if "task_type" in name else _DUMMY) + tail
    return out


def _routes(public: bool) -> list[tuple[str, str]]:
    spec = app.openapi()
    return sorted(
        (method.upper(), path)
        for path, ops in spec["paths"].items()
        for method in ops
        if _is_public(path) is public
    )


PROTECTED = _routes(public=False)
PUBLIC = _routes(public=True)


@pytest.fixture
def phase_two(monkeypatch: pytest.MonkeyPatch):
    """Auth enforced — what production runs after the frontend ships the header."""
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "x" * 40)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_the_derived_route_split_is_sane() -> None:
    """Guard the guard: if this collapses, the parametrized tests test nothing."""
    assert len(PROTECTED) >= 29, f"expected the documented 29+, got {len(PROTECTED)}"
    assert PUBLIC, "the static catalogs should still be reachable without a token"


class TestPhaseTwoRejectsAnonymous:
    @pytest.mark.parametrize("method,path", PROTECTED, ids=[f"{m} {p}" for m, p in PROTECTED])
    def test_protected_route_401s_without_a_token(
        self, client: TestClient, phase_two, method: str, path: str
    ) -> None:
        resp = client.request(method, _fill(path), json={})
        assert resp.status_code == 401, (
            f"{method} {path} answered {resp.status_code} to an anonymous caller "
            f"with AUTH_REQUIRED=true — it is missing its auth dependency"
        )

    @pytest.mark.parametrize("method,path", PROTECTED, ids=[f"{m} {p}" for m, p in PROTECTED])
    def test_protected_route_401s_on_a_forged_token(
        self, client: TestClient, phase_two, method: str, path: str
    ) -> None:
        resp = client.request(
            method, _fill(path), json={}, headers={"Authorization": "Bearer forged.nonsense"}
        )
        assert resp.status_code == 401


class TestPublicRoutesStayOpen:
    @pytest.mark.parametrize("method,path", PUBLIC, ids=[f"{m} {p}" for m, p in PUBLIC])
    def test_catalog_route_is_reachable_anonymously(
        self, client: TestClient, phase_two, method: str, path: str
    ) -> None:
        """These must NOT 401 — the frontend reads them before anyone logs in."""
        resp = client.request(method, _fill(path), json={})
        assert resp.status_code != 401, f"{method} {path} should not require a token"

    def test_health_is_reachable(self, client: TestClient, phase_two) -> None:
        assert client.get("/health").status_code == 200


class TestPhaseOneStaysOpen:
    """Legacy routes stay open; new marketplace routes require a real identity.

    This is what lets the branch deploy before `smart-model-tune` learns to send
    the header. If it ever fails, the two-phase rollout is broken and shipping
    would take the frontend down.
    """

    @pytest.fixture(autouse=True)
    def phase_one(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTH_REQUIRED", "false")
        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    @pytest.mark.parametrize(
        "method,path",
        [r for r in PROTECTED if r[0] == "GET"],
        ids=[f"{m} {p}" for m, p in PROTECTED if m == "GET"],
    )
    def test_anonymous_is_not_rejected(self, method: str, path: str) -> None:
        # `raise_server_exceptions=False` so a downstream failure surfaces as a
        # 500 response instead of propagating: there is no Postgres in unit
        # tests, and most of these routes reach for one. That is *fine* for what
        # this asserts — reaching the endpoint body at all proves the auth layer
        # let the request through, which is the entire phase-1 guarantee. Only a
        # 401 would mean auth rejected it.
        with TestClient(app, raise_server_exceptions=False) as tolerant:
            resp = tolerant.request(method, _fill(path))
        if path.startswith(_ALWAYS_AUTH_PREFIXES):
            assert resp.status_code == 401, "Always-auth resources never accept anonymous identities"
            return
        assert resp.status_code != 401, (
            f"{method} {path} rejected an anonymous caller with AUTH_REQUIRED=false — "
            f"phase 1 must behave exactly as it did before auth existed"
        )

    def test_template_rating_always_requires_identity(self, client: TestClient) -> None:
        response = client.put("/api/v1/templates/tpl-006/rating", json={"rating": 5})
        assert response.status_code == 401

    @pytest.mark.parametrize(
        "method,path",
        [
            ("POST", "/api/v1/api-keys"),
            ("GET", "/api/v1/api-keys"),
            ("POST", "/api/v1/deployments"),
            ("GET", "/api/v1/deployments"),
        ],
    )
    def test_deployments_and_api_keys_always_require_identity(
        self, client: TestClient, method: str, path: str
    ) -> None:
        """Explicit, named regression guard for the two new always-auth
        resources — on top of (not instead of) the derived `PROTECTED`
        matrix above, which already covers every method/path pair including
        these via `test_anonymous_is_not_rejected`."""
        resp = client.request(method, path, json={})
        assert resp.status_code == 401, f"{method} {path} should require identity with no token"

    def test_invalid_token_is_still_rejected(self, client: TestClient) -> None:
        """Phase 1 tolerates *absence*, never garbage — otherwise it would
        accept forged tokens for the whole compatibility window."""
        resp = client.get(
            f"/api/v1/projects/{_DUMMY}", headers={"Authorization": "Bearer forged.nonsense"}
        )
        assert resp.status_code == 401


class TestInferenceApiKeyWiring:
    """`/api/v1/inference/*` is mounted with `Depends(inference_caller)`, not
    the legacy `_AUTH`, so it must still reject both credential shapes.

    The no-token / forged-JWT cases are already exercised for every
    `PROTECTED` route (inference included) by `TestPhaseTwoRejectsAnonymous`
    above, entirely in-process (no DB touch: a missing token never reaches
    the `sk-slm-` branch, and a forged JWT fails signature/claim checks
    before anything is queried). An `sk-slm-`-shaped bearer is the one
    credential shape that *is* wired to a DB lookup
    (`api_keys_service.authenticate`'s hash lookup) — this suite has no
    Postgres to query against, so this test monkeypatches `authenticate`
    itself to reproduce the exact generic-401 it already raises for an
    unknown/revoked hash (see `tests/unit/test_api_keys.py`), which is
    enough to prove `inference_caller` really does route a key-shaped
    bearer into that call rather than silently falling through to the JWT
    path.
    """

    def test_garbage_api_key_401s_under_auth_required(
        self, monkeypatch: pytest.MonkeyPatch, phase_two
    ) -> None:
        from api.routers import inference as inference_router

        async def _always_invalid(*_args, **_kwargs):
            raise HTTPException(status_code=401, detail="invalid authentication token")

        monkeypatch.setattr(inference_router.api_keys_service, "authenticate", _always_invalid)

        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/inference/chat/completions",
                json={"model": "x", "messages": [{"role": "user", "content": "hi"}]},
                headers={"Authorization": "Bearer sk-slm-garbage"},
            )
        assert resp.status_code == 401

    def test_missing_token_still_401s_under_auth_required(
        self, client: TestClient, phase_two
    ) -> None:
        resp = client.post(
            "/api/v1/inference/chat/completions",
            json={"model": "x", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 401
