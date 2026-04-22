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
    rate_limit_per_min: int = 60
    contact_email: EmailStr | None = None
    created_at: datetime | None = None
    last_used_at: datetime | None = None
