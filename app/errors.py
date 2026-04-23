"""Unified error envelope for the API.

Every error response — auth failure, validation error, not-found, internal
bug, whatever — leaves the gateway with the same shape::

    {
      "error": {
        "code": "JOB_NOT_FOUND",
        "message": "Job not found.",
        "request_id": "req_01HXYZ...",
        "details": {...}        // optional structured context
      }
    }

Why a top-level ``error`` object (vs FastAPI's default ``{"detail": ...}``):

* partners can write one error handler, keyed on ``error.code``
* the shape is future-proof — sibling top-level keys (warnings, meta) can be
  added without breaking existing clients
* ``request_id`` is always present so support tickets cross-reference our logs

Routers raise :class:`APIError` with a code; everything else (FastAPI's own
``HTTPException``, Pydantic validation, unhandled exceptions) is normalised
to the same envelope by :func:`install_exception_handlers`.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ErrorCode(str, Enum):
    """Machine-readable error codes.

    Keep this list short and stable. Adding a code is a minor version bump in
    partner-docs terms; renaming one is a breaking change.
    """

    INVALID_REQUEST = "INVALID_REQUEST"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    JOB_NOT_READY = "JOB_NOT_READY"
    JOB_FAILED = "JOB_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorBody(BaseModel):
    code: ErrorCode = Field(..., description="Machine-readable error code.")
    message: str = Field(..., description="Human-readable summary.")
    request_id: str | None = Field(
        default=None,
        description="Gateway request ID — quote this in support requests.",
    )
    details: dict[str, Any] | None = Field(
        default=None,
        description="Optional structured context (field errors, job status, etc.).",
    )


class ErrorEnvelope(BaseModel):
    """Top-level error response body."""

    error: ErrorBody


class APIError(HTTPException):
    """HTTPException that knows its ``ErrorCode`` and optional structured details.

    Raise this from any router/handler instead of raw ``HTTPException``::

        raise APIError(
            status_code=404,
            code=ErrorCode.JOB_NOT_FOUND,
            message="Job not found.",
        )
    """

    def __init__(
        self,
        *,
        status_code: int,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=message, headers=headers)
        self.code = code
        self.message = message
        self.details = details


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _envelope(
    *,
    code: ErrorCode,
    message: str,
    request_id: str | None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = ErrorBody(
        code=code,
        message=message,
        request_id=request_id,
        details=details,
    )
    return {"error": body.model_dump(exclude_none=True)}


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------
async def handle_api_error(request: Request, exc: APIError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(
            code=exc.code,
            message=exc.message,
            request_id=_request_id(request),
            details=exc.details,
        ),
        headers=exc.headers or None,
    )


async def handle_http_exception(
    request: Request, exc: HTTPException
) -> JSONResponse:
    """Catch FastAPI's stock ``HTTPException`` (raised by framework internals,
    third-party deps, or routers that haven't been migrated yet) and normalise
    it to the envelope. The heuristic for ``code`` maps by status class."""
    code = _code_from_status(exc.status_code)
    # Preserve the old ``detail`` shape under ``details.legacy_detail`` when it
    # isn't a simple string, so we never drop information.
    details: dict[str, Any] | None = None
    message: str
    if isinstance(exc.detail, str):
        message = exc.detail or code.value
    else:
        message = code.value
        details = {"legacy_detail": exc.detail}
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(
            code=code,
            message=message,
            request_id=_request_id(request),
            details=details,
        ),
        headers=exc.headers or None,
    )


async def handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Pydantic/FastAPI request-model validation failures.

    We surface the raw Pydantic error list under ``details.field_errors`` so
    partners can point at the exact field; ``message`` stays a short summary.
    """
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=_envelope(
            code=ErrorCode.INVALID_REQUEST,
            message="Request body failed validation.",
            request_id=_request_id(request),
            details={"field_errors": exc.errors()},
        ),
    )


async def handle_unexpected(
    request: Request, exc: Exception
) -> JSONResponse:
    """Catch-all for anything we didn't anticipate. Logs full traceback; the
    partner never sees the underlying exception class or message."""
    logger.exception(
        "unhandled_exception request_id=%s path=%s",
        _request_id(request),
        request.url.path,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_envelope(
            code=ErrorCode.INTERNAL_ERROR,
            message="An unexpected error occurred.",
            request_id=_request_id(request),
        ),
    )


def _code_from_status(status_code: int) -> ErrorCode:
    if status_code == 401:
        return ErrorCode.UNAUTHORIZED
    if status_code == 403:
        return ErrorCode.FORBIDDEN
    if status_code == 404:
        return ErrorCode.JOB_NOT_FOUND
    if status_code == 409:
        return ErrorCode.JOB_NOT_READY
    if status_code == 422:
        return ErrorCode.INVALID_REQUEST
    if status_code == 429:
        return ErrorCode.RATE_LIMITED
    if 500 <= status_code < 600:
        return ErrorCode.INTERNAL_ERROR
    return ErrorCode.INVALID_REQUEST


def install_exception_handlers(app: FastAPI) -> None:
    """Wire all four handlers into ``app``.

    Called from ``create_app`` — the order matters only in that ``APIError``
    must be registered before its parent ``HTTPException``.
    """
    app.add_exception_handler(APIError, handle_api_error)  # type: ignore[arg-type]
    app.add_exception_handler(HTTPException, handle_http_exception)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, handle_validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, handle_unexpected)


# ---------------------------------------------------------------------------
# Helpers routers use to build ``responses={...}`` for OpenAPI docs
# ---------------------------------------------------------------------------
def openapi_error_responses(
    *statuses: int,
) -> dict[int | str, dict[str, Any]]:
    """Return a ``responses`` dict suitable for FastAPI route decorators.

    Every listed status renders as an :class:`ErrorEnvelope` in the OpenAPI
    schema, so partners see the concrete error shape in Swagger.
    """
    out: dict[int | str, dict[str, Any]] = {}
    for s in statuses:
        out[s] = {
            "model": ErrorEnvelope,
            "description": _DEFAULT_DESCRIPTIONS.get(s, "Error"),
        }
    return out


_DEFAULT_DESCRIPTIONS: dict[int, str] = {
    400: "Bad request.",
    401: "Missing or invalid API key.",
    403: "Authenticated but not authorised.",
    404: "Resource not found.",
    409: "Conflict with current resource state.",
    422: "Request body failed validation.",
    429: "Rate limit exceeded.",
    500: "Unexpected server error.",
}
