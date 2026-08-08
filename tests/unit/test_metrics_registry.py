"""Guards for api/core/metrics.py (M1 "metrics-core").

Four things this module promises, each with its own check below:

  * `render()` produces valid Prometheus text exposition format.
  * `resolve_route_label()` returns route *templates*, never raw request
    paths — this is the whole point of the function (unbounded label
    cardinality is a real outage class for a metrics backend), so a raw path
    containing an id must never leak into a rendered label value.
  * `observe_http()` collapses a numeric status code to the right class.
  * Every metric registered on the private registry only ever uses labels
    from the fixed, non-tenant-scoped set the plan allows.

Found-by-review history (do not "simplify" this file back to the bug):
the first version of `_build_test_app()` below built a bare `FastAPI()` with
a route declared directly on the app — a shape `api.main.app` never has,
since every router in `api/routers/*.py` is mounted via `include_router()`
(one level of `_IncludedRouter` wrapping, relative `path_format`s). That
fixture made `resolve_route_label` look correct while the real app resolved
every `/api/v1/*` request to `"unmatched"` — 47 of 48 operations. `_build_test_app`
now nests a router the same way production does, and
`test_real_app_resolves_a_nested_router_route` below checks `api.main.app`
directly so a future fixture drift can't hide the same bug again.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, FastAPI
from prometheus_client.parser import text_string_to_metric_families

from api.core.metrics import (
    _REGISTRY,
    observe_http,
    render,
    resolve_route_label,
    slm_http_requests_total,
)

# `api.main` requires a parseable DATABASE_URL at import time — conftest.py
# sets a benign default, but this file imports api.main directly (to exercise
# the REAL app, see the module docstring's history note below) rather than
# only going through fixtures, so guard the default here too in case this
# file is ever collected standalone.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test_unused"
)

# ---- (a) render() output is valid Prometheus exposition format --------------


def test_render_output_parses_as_prometheus_text() -> None:
    body, content_type = render()
    assert content_type.startswith("text/plain")

    families = list(text_string_to_metric_families(body.decode("utf-8")))
    # Non-vacuity: an empty registry would also "parse", which would make this
    # test pass even if generate_latest() silently returned nothing.
    assert len(families) > 0


# ---- (b) resolve_route_label returns templates, never raw paths -------------


def _build_test_app() -> FastAPI:
    """Mirror `api.main.app`'s ACTUAL route topology: every real router is
    mounted via `include_router(prefix=...)`, never declared on the app
    directly. A route declared straight on `app` (the pre-review-fix version
    of this fixture) exercises a code path `resolve_route_label` never hits
    in production and would pass even if the include_router recursion were
    deleted entirely."""
    app = FastAPI()
    router = APIRouter()

    @router.get("/{project_id}")
    async def get_project(project_id: str):  # pragma: no cover - never called
        return {"id": project_id}

    app.include_router(router, prefix="/api/v1/projects")
    return app


def _matched_scope(app: FastAPI, path: str) -> dict:
    """Build a scope the way the real router would, including
    `scope["endpoint"]` — by actually running one ASGI request through the
    app, rather than hand-rolling `route.matches(scope)` per top-level route.

    That hand-rolled version was tried first and is why this comment exists:
    on this FastAPI version, `_IncludedRouter.matches()` (the wrapper every
    `include_router()`-mounted router gets on `app.routes`) deliberately
    returns an EMPTY child scope — `return match, {}` in FastAPI's own
    source — because real endpoint resolution happens in `.handle()` via an
    internal `effective_candidates()` mechanism, not in `.matches()`.
    Iterating `app.routes` and calling `.matches()` therefore reports a
    match (`Match.FULL`) but never populates `scope["endpoint"]` for any
    nested route, silently degrading this whole file's coverage back to
    only the routes declared directly on `app` (`/`, `/health`, `/ready`,
    `/metrics`) — the exact shape of bug this file exists to catch.
    Running a real request sidesteps needing to know how a given FastAPI
    version's internal matching works at all: ASGI mutates `scope` in
    place, so the same dict object passed in still holds `endpoint` once
    the app returns.
    """
    import anyio

    scope: dict = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
        "query_string": b"",
        "client": ("test", 0),
    }

    async def _run() -> None:
        async def receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(_message: dict) -> None:
            pass

        await app(scope, receive, send)

    anyio.run(_run)
    return scope


def test_resolve_route_label_returns_path_template_for_matched_request() -> None:
    app = _build_test_app()
    raw_path = "/api/v1/projects/11111111-1111-1111-1111-111111111111"

    scope = _matched_scope(app, raw_path)
    assert resolve_route_label(scope, app) == "/api/v1/projects/{project_id}"


def test_resolve_route_label_returns_unmatched_for_unrouted_request() -> None:
    app = _build_test_app()

    scope = _matched_scope(app, "/no/such/route")
    assert resolve_route_label(scope, app) == "unmatched"


def test_raw_path_with_uuid_never_appears_in_a_rendered_label_value() -> None:
    app = _build_test_app()
    raw_path = "/api/v1/projects/22222222-2222-2222-2222-222222222222"

    scope = _matched_scope(app, raw_path)
    route_label = resolve_route_label(scope, app)

    observe_http(route=route_label, method="GET", status_code=200, duration_seconds=0.01)

    body, _ = render()
    text = body.decode("utf-8")
    assert raw_path not in text
    assert "22222222-2222-2222-2222-222222222222" not in text
    assert route_label in text


def test_real_app_resolves_a_nested_router_route() -> None:
    """The regression case: check `api.main.app` itself, not a stand-in.

    Every business router in this codebase is mounted via
    `include_router(prefix=...)`, so this is the only test in the file that
    would have caught the review-found bug where `_build_endpoint_map`
    scanned `app.routes` one level deep and silently mapped nothing past the
    8 top-level routes (`/`, `/health`, `/ready`, `/metrics`, docs) —
    collapsing every `/api/v1/*` operation onto `route="unmatched"`.

    Deliberately does NOT dispatch a request through `api.main.app` — it
    resolves against the endpoint map instead. An earlier version of this
    test called `_matched_scope(real_app, ...)`, i.e. ran a real ASGI
    request through the production app inside a throwaway `anyio.run()`
    loop. That passed locally and on this file alone, but poisoned
    `tests/unit/test_ws_auth.py` when the whole suite ran **inside the api
    container** — where `DATABASE_URL` points at a real Postgres, so
    touching the app binds its global async engine to an event loop that is
    dead by the time a later test uses it. Found on the box, never locally:
    the laptop's dummy DSN fails fast enough to leave nothing bound.
    End-to-end dispatch through the real app is still covered, in the one
    place it belongs — `tests/unit/test_metrics_endpoint.py::
    test_a_nested_router_request_is_observed_with_its_real_template`, which
    uses `TestClient` and manages its own loop.
    """
    from api.core.metrics import _build_endpoint_map
    from api.main import app as real_app

    # `/api/v1/tasks` (api/routers/tasks_meta.py) is mounted via
    # `include_router()` like every other business route.
    mapping = _build_endpoint_map(real_app)
    templates = set(mapping.values())
    assert "/api/v1/tasks" in templates, (
        "the real app's endpoint map has no /api/v1/tasks template — every "
        "include_router()-mounted route would resolve to 'unmatched'"
    )

    # And the lookup path itself: feed the endpoint the router really holds
    # (as `scope["endpoint"]` would be at request time) back through the
    # public resolver.
    endpoint = next(ep for ep, tpl in mapping.items() if tpl == "/api/v1/tasks")
    assert resolve_route_label({"endpoint": endpoint}, real_app) == "/api/v1/tasks"


def test_two_levels_of_include_router_both_resolve() -> None:
    """Guards against a future 'simplification' back to a single hardcoded
    recursion level: production currently nests only one level deep, so a
    non-recursive `_walk_routes` that just special-cased one level would
    pass every other test in this file. Build a router-of-a-router (2 levels
    of `include_router`, non-empty prefix at each) and assert both the
    outer- and inner-level routes resolve to their fully-accumulated
    templates."""
    from api.core.metrics import _build_endpoint_map

    app = FastAPI()
    outer = APIRouter()
    inner = APIRouter()

    @inner.get("/{item_id}")
    async def get_item(item_id: str):  # pragma: no cover - never called
        return {"id": item_id}

    @outer.get("/summary")
    async def get_summary():  # pragma: no cover - never called
        return {}

    outer.include_router(inner, prefix="/items")
    app.include_router(outer, prefix="/api/v2/widgets")

    mapping = _build_endpoint_map(app)
    templates = set(mapping.values())
    assert "/api/v2/widgets/summary" in templates
    assert "/api/v2/widgets/items/{item_id}" in templates


def test_endpoint_map_is_not_vacuously_small() -> None:
    """Non-vacuity backstop: an endpoint map that silently stopped recursing
    into `include_router()`-mounted sub-routers would still "work" for the
    handful of top-level routes (/, /health, /ready, /metrics, docs) — this
    asserts the real app's map is large enough that it could only be built by
    actually walking every included router, not just app.routes' top level.
    """
    from api.core.metrics import _build_endpoint_map
    from api.main import app as real_app

    mapping = _build_endpoint_map(real_app)
    # 8 top-level routes alone would never clear this bar; the real app has
    # 47+ operations across its included routers plus the websocket route.
    assert len(mapping) > 40, (
        f"endpoint map has only {len(mapping)} entries — did include_router "
        "recursion regress back to a single-level scan?"
    )


# ---- (c) observe_http status-code -> status_class collapsing ---------------


def test_observe_http_collapses_status_codes_to_class() -> None:
    slm_http_requests_total.clear()

    observe_http(route="/health", method="GET", status_code=200, duration_seconds=0.001)
    observe_http(route="/missing", method="GET", status_code=404, duration_seconds=0.001)
    observe_http(route="/broken", method="GET", status_code=503, duration_seconds=0.001)

    assert (
        slm_http_requests_total.labels(route="/health", method="GET", status_class="2xx")._value.get()
        == 1
    )
    assert (
        slm_http_requests_total.labels(
            route="/missing", method="GET", status_class="4xx"
        )._value.get()
        == 1
    )
    assert (
        slm_http_requests_total.labels(
            route="/broken", method="GET", status_class="5xx"
        )._value.get()
        == 1
    )


# ---- (d) every registered metric's labels are a subset of the allowed set --


_ALLOWED_LABELS = {
    "route",
    "method",
    "status_class",
    "queue",
    "type",
    "stage",
    "model",
    "outcome",
    "dependency",
    "error_type",
}


def test_registered_metric_labels_are_a_subset_of_the_allowed_non_tenant_set() -> None:
    # Enumerate the actual collector objects (not `.collect()` samples) so
    # this catches a bad label declaration even on a metric nothing has
    # observed yet — e.g. slm_worker_up before any worker has reported in.
    collectors = list(_REGISTRY._collector_to_names.keys())
    # Non-vacuity backstop: if the registry were empty (metrics.py failed to
    # register anything) this assertion would pass trivially and hide it.
    assert len(collectors) > 0

    for collector in collectors:
        label_names = set(collector._labelnames)
        assert label_names <= _ALLOWED_LABELS, (
            f"{collector} declares disallowed label(s) "
            f"{label_names - _ALLOWED_LABELS} (no per-tenant labels allowed: "
            f"no project/actor/user/job_id)"
        )
