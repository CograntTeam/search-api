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
    airtable_companies_table_id: str = Field(
        ..., pattern=r"^tbl[A-Za-z0-9]{14}$"
    )

    # n8n
    n8n_webhook_base_url: str
    n8n_search_path: str = "search-initiation"

    # Internal shared secret for n8n -> gateway callbacks
    internal_shared_secret: str = Field(..., min_length=16)

    # Grants table — read/written by the in-process reverse search. Hardcoded as
    # the default (it's an identifier, not a secret) so the app always has it and
    # reverse search runs without any env var; an AIRTABLE_GRANTS_TABLE_ID env var
    # may still override it but isn't required. The type stays optional and the
    # scheduler/repo still tolerate an absent value (defense in depth). Lives
    # outside AIRTABLE_SCHEMA.md's partner-API set.
    airtable_grants_table_id: str | None = Field(
        default="tblnRV4RxCOv7X5u6", pattern=r"^tbl[A-Za-z0-9]{14}$"
    )

    # --- Reverse search (in-process APScheduler) ---
    # Disabled in tests; the lifespan hook only starts the scheduler when true.
    scheduler_enabled: bool = True
    scheduler_timezone: str = "Europe/Vilnius"
    reverse_search_poll_seconds: int = 120
    # Bound concurrent Gemini calls so we never fan out past Airtable's 5 req/s.
    reverse_search_concurrency: int = 5
    # Safety valve: cap the companies sanity-checked (Gemini-called) per grant so
    # one broad grant can't fan out into a huge token burst. 0 = no cap.
    reverse_search_max_companies: int = 50

    # --- Gemini sanity-check LLM ---
    # Optional so the app still boots without it; the scheduler logs and skips
    # reverse-search runs when the key is absent.
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash"

    # --- Daily client-notification digest (Workspace SMTP, app password) ---
    daily_email_hour: int = 15
    daily_email_minute: int = 0
    email_enabled: bool = True
    # Pilot mode: route every recipient to email_from so you can eyeball real
    # emails before switching on real client delivery.
    email_dry_run: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None  # Google Workspace app password
    email_from: str = "info@cogrant.eu"
    email_reply_to: str = "info@cogrant.eu"
    email_bcc: str | None = "info@cogrant.eu"
    # Client portal (planner) base. The digest links every opportunity card and
    # the primary CTA here; override per environment if the host changes.
    client_portal_url: str | None = "https://planner.cogrant.eu"

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
