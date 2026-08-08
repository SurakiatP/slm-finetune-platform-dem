"""Unit tests for `GET /metrics` (M8 "metrics-endpoint-and-http-instrumentation").

`api/main.py` wires two things together on this route:
  * the HTTP instrumentation added inside `_request_context_middleware`
    (`api.core.metrics.observe_http` / `resolve_route_label`), and
  * `api/services/metrics_export.refresh()`, called once per scrape to
    re-derive the scrape-time gauges before rendering.

Covered here:
  1. The endpoint itself: 200, correct content-type, parseable exposition.
  2. It is reachable with no token even when `AUTH_REQUIRED=true` — same
     class of infrastructure probe as `/health`/`/ready` (see
     `tests/unit/test_auth_enforcement.py`, which now also lists it in
     `_PUBLIC_EXACT`).
  3. The HTTP instrumentation wired into `_request_context_middleware`
     actually observes real requests (`/health` counted, status_class 2xx).
  4. `resolve_route_label` degrades a nonexistent path to `"unmatched"`,
     never the raw request path — the whole reason that function exists.
  5. `refresh()` is contractually not allowed to raise (see its own
     docstring), but the endpoint must survive even if it did.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from api.core.config import get_settings
from api.main import app
from api.services import metrics_export


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _families(text: str) -> dict[str, object]:
    return {family.name: family for family in text_string_to_metric_families(text)}


# ---- 1. basic shape ----------------------------------------------------------


def test_metrics_returns_200_with_prometheus_content_type_and_parseable_body(
    client: TestClient,
) -> None:
    resp = client.get("/metrics")
    assert resp.status_code == 200
    # `api.core.metrics.render()` returns `prometheus_client.CONTENT_TYPE_LATEST`
    # verbatim (see `tests/unit/test_metrics_registry.py`, which asserts the
    # same `startswith("text/plain")` rather than pinning an exact
    # `version=...` — the installed `prometheus_client` (0.26.0) emits
    # `text/plain; version=1.0.0; charset=utf-8`, not the older 0.0.4 exposition
    # version string).
    assert resp.headers["content-type"].startswith("text/plain")

    families = list(text_string_to_metric_families(resp.text))
    # Non-vacuity: an empty body would also "parse" as zero families, which
    # would make this test pass even if render() came back empty.
    assert len(families) > 0


# ---- 2. reachable without auth, even in phase 2 -------------------------------


def test_metrics_reachable_without_a_token_even_with_auth_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "x" * 40)
    get_settings.cache_clear()
    try:
        with TestClient(app) as anonymous:
            resp = anonymous.get("/metrics")
        assert resp.status_code == 200
    finally:
        get_settings.cache_clear()


# ---- 3. HTTP instrumentation wired into the outer middleware ------------------


def test_health_requests_are_observed_in_the_http_counter(client: TestClient) -> None:
    client.get("/health")
    client.get("/health")

    text = client.get("/metrics").text
    families = _families(text)
    # `text_string_to_metric_families` names a Counter family after its
    # metric root, stripping the `_total` suffix that only appears on the
    # individual sample name in the exposition text (`slm_http_requests_total`
    # the Python object -> family "slm_http_requests" -> sample
    # "slm_http_requests_total").
    counter = families["slm_http_requests"]
    matching = [
        s
        for s in counter.samples
        if s.name == "slm_http_requests_total" and s.labels.get("route") == "/health"
    ]
    assert matching, "no slm_http_requests_total series for route=/health"
    assert matching[0].labels.get("status_class") == "2xx"
    assert matching[0].labels.get("method") == "GET"
    assert matching[0].value >= 2


# ---- 3b. a route mounted via include_router() gets its real template ----------
#
# Regression case (found in review): `/health` is declared directly on
# `app`, so a test that only ever hits `/health` cannot tell a working
# `resolve_route_label` apart from one that maps just the app's top-level
# routes and silently treats every `include_router()`-mounted route (i.e.
# every `/api/v1/*` operation — 47 of the app's 48 non-root operations) as
# "unmatched". `/api/v1/tasks` has no auth dependency (see api/routers/
# tasks_meta.py) so it can be hit with no token, same as `/health`.


def test_a_nested_router_request_is_observed_with_its_real_template(
    client: TestClient,
) -> None:
    client.get("/api/v1/tasks")

    text = client.get("/metrics").text
    families = _families(text)
    counter = families["slm_http_requests"]
    matching = [
        s
        for s in counter.samples
        if s.name == "slm_http_requests_total"
        and s.labels.get("route") == "/api/v1/tasks"
    ]
    assert matching, (
        "no slm_http_requests_total series for route=/api/v1/tasks — "
        "a nested (include_router-mounted) route resolved to something "
        "other than its real template, most likely 'unmatched'"
    )
    assert matching[0].labels.get("status_class") == "2xx"


# ---- 4. unmatched paths never leak the raw path into a label ------------------


def test_nonexistent_path_is_labelled_unmatched_not_the_raw_path(client: TestClient) -> None:
    raw_path = "/no/such/route/at/all"
    resp = client.get(raw_path)
    assert resp.status_code == 404

    text = client.get("/metrics").text
    assert raw_path not in text
    assert 'route="unmatched"' in text


# ---- 5. refresh() raising must not turn the scrape into a 500 ----------------


async def _raise(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError("refresh exploded")


def test_metrics_still_200s_if_refresh_raises(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(metrics_export, "refresh", _raise)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert list(text_string_to_metric_families(resp.text))
