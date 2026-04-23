"""Application configuration, loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. Populated from environment / .env at startup."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Runtime
    env: Literal["development", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    port: int = 8000

    # Public URL (used when instructing n8n where to call back)
    api_base_url: str = "http://localhost:8000"

    # Airtable
    airtable_pat: str = Field(..., min_length=1)
    airtable_base_id: str = Field(..., pattern=r"^app[A-Za-z0-9]{14}$")
    airtable_api_keys_table_id: str = Field(..., pattern=r"^tbl[A-Za-z0-9]{14}$")
    airtable_api_jobs_table_id: str = Field(..., pattern=r"^tbl[A-Za-z0-9]{14}$")
    airtable_search_matches_table_id: str = Field(
        ..., pattern=r"^tbl[A-Za-z0-9]{14}$"
    )

    # n8n
    n8n_webhook_base_url: str
    n8n_search_path: str = "search-initiation"

    # Internal shared secret for n8n -> gateway callbacks
    internal_shared_secret: str = Field(..., min_length=16)

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    def n8n_webhook_url(self, path: str) -> str:
        """Build a full webhook URL by joining the base with a path segment."""
        base = self.n8n_webhook_base_url.rstrip("/")
        return f"{base}/{path.lstrip('/')}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached singleton. Use this everywhere in the app."""
    return Settings()  # type: ignore[call-arg]
