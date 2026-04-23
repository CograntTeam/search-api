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

from fastapi import Depends, Header, Request, status
from fastapi.security import HTTPBearer

from app.config import Settings, get_settings
from app.errors import APIError, ErrorCode
from app.models.keys import ApiKey, KeyStatus
from app.ratelimit import (
    InMemoryRateLimiter,
    get_limiter,
    windows_for,
    windows_for_searches,
)
from app.repositories.airtable import AirtableRepo

# Registered solely for OpenAPI — the "Authorize" button in Swagger UI
# depends on a security scheme existing. We still authenticate via our own
# ``require_api_key`` dep (which reads the raw ``Authorization`` header),
# so the actual verification path stays unchanged.
bearer_scheme = HTTPBearer(
    scheme_name="PartnerApiKey",
    description=(
        "Your partner API key, passed as ``Authorization: Bearer <key>``. "
        "Contact Cogrant to issue or rotate."
    ),
    auto_error=False,
)

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
        raise APIError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=ErrorCode.UNAUTHORIZED,
            message="Missing Authorization header.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise APIError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=ErrorCode.UNAUTHORIZED,
            message="Authorization header must be 'Bearer <api-key>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return parts[1].strip()


async def require_api_key(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    repo: AirtableRepo = Depends(get_repo),
    limiter: InMemoryRateLimiter = Depends(get_limiter),
) -> ApiKey:
    """FastAPI dependency enforcing auth **and** rate limits.

    We combine the two so every protected route gets both for free — and
    so the 429 response always carries the same ``request_id`` / envelope
    shape as every other error on the gateway.
    """
    plaintext = _parse_bearer(authorization)
    key_hash = _hash_key(plaintext)
    record = repo.find_key_by_hash(key_hash)
    if record is None:
        logger.info("auth.reject reason=unknown_key prefix=%s", plaintext[:8])
        raise APIError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=ErrorCode.UNAUTHORIZED,
            message="Invalid API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if record.status != KeyStatus.ACTIVE.value:
        logger.info(
            "auth.reject reason=revoked partner=%s", record.partner_name
        )
        raise APIError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=ErrorCode.UNAUTHORIZED,
            message="API key is revoked.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Enforce multi-window rate limits. We hash-lookup auth first so
    # unauthenticated traffic never consumes a partner's quota.
    windows = windows_for(
        per_min=record.rate_limit_per_min,
        per_day=record.rate_limit_per_day,
        per_week=record.rate_limit_per_week,
    )
    decision = limiter.check(record.record_id, windows)
    if not decision.allowed and decision.tripped is not None:
        retry_after = max(1, int(decision.tripped.reset_in_seconds) + 1)
        logger.info(
            "ratelimit.block partner=%s window=%s limit=%s retry_after=%s",
            record.partner_name,
            decision.tripped.name,
            decision.tripped.limit,
            retry_after,
        )
        raise APIError(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code=ErrorCode.RATE_LIMITED,
            message=(
                f"Rate limit exceeded for the '{decision.tripped.name}' window."
            ),
            details={
                "window": decision.tripped.name,
                "limit": decision.tripped.limit,
                "retry_after_seconds": retry_after,
                "windows": [
                    {
                        "name": w.name,
                        "limit": w.limit,
                        "remaining": w.remaining,
                    }
                    for w in decision.windows
                ],
            },
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(decision.tripped.limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Window": decision.tripped.name,
                "X-RateLimit-Reset": str(retry_after),
            },
        )

    # Stash the bottleneck window on request.state so the access-log
    # middleware can decorate the eventual response with ``X-RateLimit-*``
    # headers. Doing it this way — instead of via the injected Response —
    # ensures the headers appear on exception-handler JSONResponses too.
    tightest = decision.tightest
    if tightest is not None:
        request.state.rl_tightest = tightest

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
        raise APIError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=ErrorCode.UNAUTHORIZED,
            message="Invalid internal secret.",
        )


# ---------------------------------------------------------------------------
# Search-creation-specific rate limit
# ---------------------------------------------------------------------------
def enforce_search_creation_limit(
    request: Request,
    api_key: Annotated[ApiKey, Depends(require_api_key)],
    limiter: Annotated[InMemoryRateLimiter, Depends(get_limiter)],
) -> ApiKey:
    """Enforce the ``searches_per_day`` / ``searches_per_week`` caps.

    Lives in its own bucket keyed ``<record_id>:searches`` so it does
    NOT interfere with the general all-routes windows enforced by
    :func:`require_api_key`. Called only from
    ``POST /v1/searches``. Polling status and fetching matches skip this
    check entirely.

    If the search bucket is the tightest on the happy path, it replaces
    whatever window the general limiter stashed — so ``X-RateLimit-*``
    headers always point at the bottleneck the partner should actually
    care about.
    """
    search_windows = windows_for_searches(
        per_day=api_key.searches_per_day,
        per_week=api_key.searches_per_week,
    )
    if not search_windows:
        return api_key

    decision = limiter.check(f"{api_key.record_id}:searches", search_windows)
    if not decision.allowed and decision.tripped is not None:
        retry_after = max(1, int(decision.tripped.reset_in_seconds) + 1)
        logger.info(
            "ratelimit.block partner=%s bucket=searches window=%s limit=%s retry_after=%s",
            api_key.partner_name,
            decision.tripped.name,
            decision.tripped.limit,
            retry_after,
        )
        raise APIError(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code=ErrorCode.RATE_LIMITED,
            message=(
                f"Search creation rate limit exceeded for the "
                f"'{decision.tripped.name}' window."
            ),
            details={
                "bucket": "searches",
                "window": decision.tripped.name,
                "limit": decision.tripped.limit,
                "retry_after_seconds": retry_after,
                "windows": [
                    {
                        "name": w.name,
                        "limit": w.limit,
                        "remaining": w.remaining,
                    }
                    for w in decision.windows
                ],
            },
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(decision.tripped.limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Window": decision.tripped.name,
                "X-RateLimit-Reset": str(retry_after),
            },
        )

    # Happy-path headers: prefer the search-bucket bottleneck when it is
    # tighter than whatever the general limiter stashed. Headers are set
    # via the access-log middleware reading ``request.state.rl_tightest``.
    tightest = decision.tightest
    if tightest is not None:
        current = getattr(request.state, "rl_tightest", None)
        if current is None or tightest.remaining < current.remaining:
            request.state.rl_tightest = tightest

    return api_key
