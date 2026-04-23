"""HTTP middleware — request ID propagation and access logging.

Both middlewares are thin by design. They touch ``request.state`` so that
downstream dependencies, handlers, and logging filters can read the same
fields without any explicit wiring.
"""

from __future__ import annotations

import logging
import secrets
import time
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

logger = logging.getLogger("app.access")

# Populated per-request by :class:`RequestIDMiddleware`. Logging filters and
# background tasks can read it with ``current_request_id.get()``.
current_request_id: ContextVar[str | None] = ContextVar(
    "current_request_id", default=None
)


def _new_request_id() -> str:
    """Generate a short, URL-safe request ID.

    16 random bytes as base32 → 26 chars without padding. Prefix keeps the ID
    self-identifying in logs even when it's pasted without context.
    """
    token = secrets.token_hex(8)  # 16 hex chars — easy to grep
    return f"req_{token}"


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Ensure every request has an ID and echo it back on the response.

    If the caller sends ``X-Request-Id``, we honour it (handy for tracing a
    flow that already has one — e.g. when a partner wraps us in their own
    observability stack). Otherwise we generate one.

    The ID lives on ``request.state.request_id`` and in the ``X-Request-Id``
    response header; it is also pushed into the :data:`current_request_id`
    contextvar so logging filters can pick it up with zero plumbing.
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        incoming = request.headers.get("x-request-id")
        request_id = incoming.strip() if incoming and incoming.strip() else _new_request_id()
        # Cap length to avoid log pollution if a partner sends something huge.
        if len(request_id) > 80:
            request_id = request_id[:80]
        request.state.request_id = request_id
        token = current_request_id.set(request_id)
        try:
            response = await call_next(request)
        finally:
            current_request_id.reset(token)
        response.headers["X-Request-Id"] = request_id
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """One structured log line per request + rate-limit header decoration.

    Skips ``/health`` for logging to keep the log stream readable under
    constant polling from Render + uptime monitors.

    On every request (including error responses from the exception
    handlers), this middleware reads any :class:`~app.ratelimit.WindowState`
    that :func:`~app.security.require_api_key` stashed on
    ``request.state.rl_tightest`` and surfaces it as ``X-RateLimit-*``
    headers. Doing it here — rather than via the injected ``Response``
    object in the dependency — is the only reliable way to get the headers
    onto the ``JSONResponse`` built by our exception handlers.
    """

    def __init__(self, app: ASGIApp, *, skip_paths: set[str] | None = None) -> None:
        super().__init__(app)
        self.skip_paths = skip_paths or {"/health"}

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        start = time.perf_counter()
        try:
            response: Response = await call_next(request)
        except Exception:
            # Let the exception propagate — the exception handler will emit
            # a 500 and log the traceback separately. We still record the
            # timing so latency metrics aren't skewed.
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.error(
                "http.request method=%s path=%s duration_ms=%s status=error",
                request.method,
                request.url.path,
                duration_ms,
            )
            raise

        # Decorate with rate-limit headers if the auth dep set one.
        tightest = getattr(request.state, "rl_tightest", None)
        if tightest is not None and "x-ratelimit-limit" not in {
            h.lower() for h in response.headers
        }:
            response.headers["X-RateLimit-Limit"] = str(tightest.limit)
            response.headers["X-RateLimit-Remaining"] = str(
                max(0, tightest.remaining)
            )
            response.headers["X-RateLimit-Window"] = tightest.name

        if request.url.path in self.skip_paths:
            return response
        duration_ms = int((time.perf_counter() - start) * 1000)
        level = (
            logging.WARNING
            if 400 <= response.status_code < 500
            else logging.ERROR
            if response.status_code >= 500
            else logging.INFO
        )
        logger.log(
            level,
            "http.request method=%s path=%s status=%s duration_ms=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response
