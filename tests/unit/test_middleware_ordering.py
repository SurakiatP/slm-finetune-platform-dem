"""`api/main.py` has a load-bearing ordering comment: `_request_context_middleware`
must stay the OUTERMOST layer in the ASGI middleware stack, so the request id
is minted (and the X-Request-ID header attached, and the request logged —
now including `client_ip`) around every other layer, including a 400 from
`TrustedHostMiddleware` or a CORS rejection.

A comment is prose, not a guarantee — the ordering has already drifted once
in spirit if not in commits (the request-context middleware was originally
registered "after CORSMiddleware", and a well-intentioned future edit that
adds a new `app.add_middleware(...)` call *after* the
`@app.middleware("http")` decorator would silently break it, since Starlette
inserts each newly-registered middleware at the front of the stack). This
file reads `app.user_middleware` directly — the actual fact Starlette will
build the ASGI app from — instead of trusting the comment.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from api.main import _request_context_middleware, app


def _dispatch_targets() -> list:
    """The `dispatch` callable each `BaseHTTPMiddleware` entry wraps, in
    `app.user_middleware` order (index 0 = outermost — see
    `Starlette.add_middleware`, which does `insert(0, ...)`, and
    `Starlette.build_middleware_stack`, which makes index 0 the outermost
    ASGI layer)."""
    return [
        m.kwargs.get("dispatch")
        for m in app.user_middleware
        if m.cls is BaseHTTPMiddleware
    ]


class TestMiddlewareOrdering:
    def test_request_context_middleware_is_outermost(self) -> None:
        """`app.user_middleware[0]` is the outermost layer of the stack.
        Mutation check: register `_request_context_middleware` earlier
        (i.e. call `app.add_middleware`/the decorator before CORS or
        TrustedHost are added) and this goes red."""
        assert app.user_middleware, "no middleware registered at all"
        outermost = app.user_middleware[0]
        assert outermost.cls is BaseHTTPMiddleware
        assert outermost.kwargs.get("dispatch") is _request_context_middleware, (
            "the request-context middleware must be the outermost layer so "
            "X-Request-ID and the client_ip log line cover every other "
            "middleware, including a TrustedHostMiddleware 400"
        )

    def test_request_context_middleware_is_registered_exactly_once(self) -> None:
        targets = _dispatch_targets()
        assert targets.count(_request_context_middleware) == 1

    def test_trusted_host_middleware_is_present(self) -> None:
        """Mutation check: remove the `app.add_middleware(TrustedHostMiddleware,
        ...)` call in `api/main.py` and this goes red."""
        classes = [m.cls for m in app.user_middleware]
        assert TrustedHostMiddleware in classes, (
            "TrustedHostMiddleware is missing from the stack — Host header "
            "validation is not enforced"
        )

    def test_trusted_host_middleware_is_not_outermost(self) -> None:
        """It must sit strictly inside `_request_context_middleware` so a
        rejected Host header still gets a request id, an X-Request-ID
        response header, and a logged client_ip."""
        classes = [m.cls for m in app.user_middleware]
        outermost = app.user_middleware[0]
        assert outermost.cls is not TrustedHostMiddleware
        assert TrustedHostMiddleware in classes

    def test_cors_middleware_is_present_and_not_outermost(self) -> None:
        outermost = app.user_middleware[0]
        classes = [m.cls for m in app.user_middleware]
        assert CORSMiddleware in classes
        assert outermost.cls is not CORSMiddleware

    def test_trusted_host_middleware_uses_configured_allowed_hosts(self) -> None:
        """Wired to `settings.api_allowed_hosts` (owned by
        `api/core/config.py`), not a literal hardcoded in `main.py`."""
        from api.core.config import get_settings

        entry = next(m for m in app.user_middleware if m.cls is TrustedHostMiddleware)
        assert entry.kwargs.get("allowed_hosts") == get_settings().api_allowed_hosts

    # ---- M8: HTTP metrics instrumentation lives inside the existing outer
    # middleware, not as a new registered layer -------------------------------

    def test_request_context_middleware_is_still_outermost_after_metrics_wiring(
        self,
    ) -> None:
        """Mutation check for the M8 change specifically: if the
        `metrics.observe_http(...)` call added to `_request_context_middleware`
        had instead been shipped as a second `@app.middleware("http")`
        (or a second `app.add_middleware(...)` call), Starlette's `insert(0,
        ...)` would make *that* the new outermost layer and this goes red —
        same assertion as `test_request_context_middleware_is_outermost`
        above, restated here so an M8 regression fails under a name that
        names the change, not just the invariant."""
        assert app.user_middleware, "no middleware registered at all"
        outermost = app.user_middleware[0]
        assert outermost.cls is BaseHTTPMiddleware
        assert outermost.kwargs.get("dispatch") is _request_context_middleware

    def test_middleware_count_did_not_grow_from_metrics_wiring(self) -> None:
        """HTTP instrumentation was added *inside* `_request_context_middleware`
        (a function body edit), so the number of registered middleware layers
        must be exactly what it was before M8: CORS, TrustedHost, and the one
        request-context `BaseHTTPMiddleware` — three, not four."""
        assert len(app.user_middleware) == 3, (
            f"expected exactly 3 registered middleware layers (CORS, "
            f"TrustedHost, request-context), got {len(app.user_middleware)}: "
            f"{[m.cls.__name__ for m in app.user_middleware]}. HTTP metrics "
            "must be wired inside the existing request-context middleware, "
            "never as a new app.middleware(...)/add_middleware(...) layer."
        )
