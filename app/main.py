"""FastAPI application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.config import get_settings
from app.logging_config import configure_logging
from app.routers import health, internal, searches


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    # Initialise any long-lived clients here as we add them (Airtable, httpx).
    yield
    # Graceful shutdown hooks go here.


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Cogrant Search API",
        version=__version__,
        description=(
            "Partner-facing API in front of Cogrant's grant-search workflows. "
            "Async job model: POST to create a job, poll the GET endpoint for "
            "results."
        ),
        docs_url="/docs" if not settings.is_production else "/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )

    app.include_router(health.router)
    app.include_router(searches.router)
    app.include_router(internal.router)

    return app


app = create_app()
