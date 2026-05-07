"""Application-wide error handlers.

Goals:
  • Every error response uses `ErrorResponse` (`{"detail", "code", "extra"}`)
    so the frontend has one shape to parse.
  • Validation errors (`RequestValidationError`) keep the per-field details
    in `extra` for form rendering, but the top-level `detail` is human-readable.
  • Unhandled exceptions return 500 + a generic message — never leak a stack
    trace to clients (it's logged server-side).
  • `HTTPException`s raised in services pass through with their status code +
    detail so domain-specific errors (404 / 409 / 422 / 502) keep their meaning.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
# Use Starlette's base class so the handler catches both
# FastAPI HTTPExceptions and routing-layer 404s/405s.
from starlette.exceptions import HTTPException

from api.schemas.responses import ErrorResponse

log = logging.getLogger("api.errors")


def install_handlers(app: FastAPI) -> None:
    """Register the three handlers below on the FastAPI app."""

    @app.exception_handler(HTTPException)
    async def _http_handler(request: Request, exc: HTTPException) -> JSONResponse:
        body = ErrorResponse(
            detail=str(exc.detail) if exc.detail else "request failed",
            code=_code_for_status(exc.status_code),
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=body.model_dump(),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Pydantic emits a list of {loc, msg, type, input?} dicts; we surface
        # the count + first message in `detail` and the full list in `extra`.
        errors = exc.errors()
        first = errors[0] if errors else {}
        loc = ".".join(str(p) for p in first.get("loc", []))
        msg = first.get("msg", "validation error")
        detail = f"{loc}: {msg}" if loc else msg
        body = ErrorResponse(
            detail=detail,
            code="validation_error",
            extra={"errors": _safe_serialize(errors)},
        )
        return JSONResponse(status_code=422, content=body.model_dump())

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Mint a correlation id so the user can quote it back; the matching
        # log line on the server has the full traceback.
        correlation_id = uuid.uuid4().hex[:12]
        log.exception(
            "unhandled exception (%s %s) — correlation=%s",
            request.method,
            request.url.path,
            correlation_id,
        )
        body = ErrorResponse(
            detail="internal server error",
            code="internal_error",
            extra={"correlation_id": correlation_id},
        )
        return JSONResponse(status_code=500, content=body.model_dump())


# ---- helpers ---------------------------------------------------------------


def _code_for_status(status_code: int) -> str:
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        413: "payload_too_large",
        422: "validation_error",
        500: "internal_error",
        501: "not_implemented",
        502: "bad_gateway",
        503: "service_unavailable",
    }.get(status_code, f"http_{status_code}")


def _safe_serialize(errors: list[dict]) -> list[dict]:
    """Drop non-JSON-serializable bits (e.g. raw Pydantic ctx exceptions)."""
    cleaned: list[dict] = []
    for err in errors:
        out = {}
        for k, v in err.items():
            if k == "ctx" and isinstance(v, dict):
                # ctx may contain Exception instances → keep only their str form.
                out[k] = {ck: str(cv) for ck, cv in v.items()}
            elif _is_json_safe(v):
                out[k] = v
            else:
                out[k] = str(v)
        cleaned.append(out)
    return cleaned


def _is_json_safe(value: object) -> bool:
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, (list, tuple)):
        return all(_is_json_safe(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_json_safe(v) for k, v in value.items())
    return False


__all__ = ["install_handlers"]
