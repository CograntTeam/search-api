"""Authentication + authorisation helpers.

Two separate auth paths:

1. **Public API** — partner requests carry ``Authorization: Bearer <plaintext-key>``.
   We SHA-256 the plaintext and look it up in Airtable's ``api_keys`` table. An
   inactive/revoked key rejects with 401.

2. **Internal callbacks** — n8n calls back into ``/internal/...`` with a shared
   secret in the ``X-Internal-Secret`` header. This is not a real API key; it's
   a single rotating value known only to the gateway and n8n.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.config import Settings, get_settings
from app.models.keys import ApiKey, KeyStatus
from app.repositories.airtable import AirtableRepo

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dependency wiring
# ---------------------------------------------------------------------------
def get_repo(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AirtableRepo:
    """FastAPI dependency that yields a request-scoped AirtableRepo.

    pyairtable's Table is thread-safe, so constructing one per request is
    cheap. If we ever move to a real connection pool we'll change it here
    only.
    """
    return AirtableRepo(settings)


# ---------------------------------------------------------------------------
# Public API: partner API key
# ---------------------------------------------------------------------------
def _hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _parse_bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header must be 'Bearer <api-key>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return parts[1].strip()


async def require_api_key(
    authorization: Annotated[str | None, Header()] = None,
    repo: AirtableRepo = Depends(get_repo),
) -> ApiKey:
    """FastAPI dependency enforcing a valid, active partner API key."""
    plaintext = _parse_bearer(authorization)
    key_hash = _hash_key(plaintext)
    record = repo.find_key_by_hash(key_hash)
    if record is None:
        logger.info("auth.reject reason=unknown_key prefix=%s", plaintext[:8])
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if record.status != KeyStatus.ACTIVE.value:
        logger.info(
            "auth.reject reason=revoked partner=%s", record.partner_name
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key is revoked.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Best-effort update; never blocks the request
    repo.touch_key_last_used(record.record_id)
    return record


# ---------------------------------------------------------------------------
# Internal callbacks: n8n shared secret
# ---------------------------------------------------------------------------
def require_internal_secret(
    settings: Annotated[Settings, Depends(get_settings)],
    x_internal_secret: Annotated[str | None, Header(alias="X-Internal-Secret")] = None,
) -> None:
    """Guard for ``/internal/...`` endpoints. Uses constant-time comparison."""
    if not x_internal_secret or not hmac.compare_digest(
        x_internal_secret, settings.internal_shared_secret
    ):
        logger.warning("internal.reject reason=bad_secret")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal secret.",
        )
