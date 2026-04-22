"""Liveness + readiness endpoints."""

from fastapi import APIRouter
from pydantic import BaseModel

from app import __version__
from app.config import get_settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    env: str
    version: str


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health() -> HealthResponse:
    """Cheap liveness check. Does not hit external dependencies.

    Used by Render's health check and UptimeRobot.
    """
    settings = get_settings()
    return HealthResponse(status="ok", env=settings.env, version=__version__)
