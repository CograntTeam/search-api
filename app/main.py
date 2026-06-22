"""FastAPI application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.config import get_settings
from app.errors import install_exception_handlers
from app.logging_config import configure_logging
from app.middleware import AccessLogMiddleware, RequestIDMiddleware
from app.routers import health, internal, landing, matches, searches

# Public OpenAPI tags — order controls sidebar grouping in Swagger.
_OPENAPI_TAGS = [
    {
        "name": "searches",
        "description": (
            "Create and poll grant-search jobs. Jobs run asynchronously — "
            "POST to create, then either poll GET or wait for your "
            "``callback_url`` to fire."
        ),
    },
    {
        "name": "health",
        "description": "Liveness and readiness probes.",
    },
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    # Initialise any long-lived clients here as we add them (Airtable, httpx).
    if settings.scheduler_enabled:
        from app.jobs.scheduler import start_scheduler

        start_scheduler(settings)
    try:
        yield
    finally:
        # Graceful shutdown hooks go here.
        if settings.scheduler_enabled:
            from app.jobs.scheduler import shutdown_scheduler

            shutdown_scheduler()


def create_app() -> FastAPI:
    get_settings()  # validate configuration at import time (fail fast)

    app = FastAPI(
        title="Cogrant Search API",
        version=__version__,
        description=(
            "Partner-facing API in front of Cogrant's grant-search workflows.\n\n"
            "**Auth.** Every request must carry a partner API key as "
            "``Authorization: Bearer <key>``.\n\n"
            "**Async jobs.** POST ``/v1/searches`` returns 202 with a "
            "``job_id``. Poll ``GET /v1/searches/{job_id}`` until "
            "``status == \"done\"``, then fetch results from "
            "``GET /v1/searches/{job_id}/matches``. Alternatively, supply "
            "``callback_url`` on creation and the gateway will POST the "
            "completed job to it.\n\n"
            "**Idempotency.** Include an ``Idempotency-Key`` header on POST "
            "requests to make them safely retryable; a replay returns the "
            "original job and sets ``Idempotency-Replayed: true`` on the "
            "response.\n\n"
            "**Errors.** Every error response uses a single envelope::\n\n"
            "```\n"
            '{"error": {"code": "JOB_NOT_FOUND", "message": "...", '
            '"request_id": "req_...", "details": {...}}}\n'
            "```\n\n"
            "Every response also carries an ``X-Request-Id`` header — quote "
            "it in support requests and it'll match our logs."
        ),
        openapi_tags=_OPENAPI_TAGS,
        contact={
            "name": "Cogrant",
            "url": "https://www.cogrant.eu",
            "email": "hello@cogrant.eu",
        },
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )

    # Order matters. ASGI middleware runs outer→inner on the way in and
    # inner→outer on the way out. We want RequestID first so AccessLog can
    # log it, and AccessLog innermost so its timer captures the full stack.
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIDMiddleware)

    install_exception_handlers(app)

    app.include_router(landing.router)
    app.include_router(health.router)
    app.include_router(searches.router)
    app.include_router(matches.router)
    app.include_router(internal.router)

    return app


app = create_app()
