"""Pydantic models for API keys (Airtable api_keys table)."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, EmailStr


class KeyStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"


class ApiKey(BaseModel):
    """An authenticated API key row as stored in Airtable."""

    model_config = ConfigDict(use_enum_values=True)

    record_id: str  # Airtable rec...
    partner_name: str
    key_hash: str
    key_prefix: str | None = None
    status: KeyStatus = KeyStatus.ACTIVE
    # Per-minute / per-day / per-week caps on *all* authenticated calls.
    # ``None`` or <= 0 disables a window for this partner.
    rate_limit_per_min: int | None = 60
    rate_limit_per_day: int | None = 500
    rate_limit_per_week: int | None = 2_000
    # Separate day / week caps specifically for ``POST /v1/searches``
    # (search *creation*). Polling status and fetching matches don't
    # count against these — they hit the general buckets above instead.
    # ``None`` or <= 0 disables that search-specific window.
    searches_per_day: int | None = None
    searches_per_week: int | None = None
    contact_email: EmailStr | None = None
    created_at: datetime | None = None
    last_used_at: datetime | None = None
