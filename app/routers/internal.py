"""Internal endpoints called by n8n when workflows finish.

n8n authenticates with a shared secret in ``X-Internal-Secret``. These routes
are not documented publicly (``include_in_schema=False``) and are not meant
for partner use.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, status
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.errors import APIError, ErrorCode
from app.models.jobs import JobStatus
from app.repositories.airtable import AirtableRepo
from app.security import get_repo, require_internal_secret
from app.services.notifications import NotificationService
from app.services.smtp_client import SmtpClient

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal",
    tags=["internal"],
    include_in_schema=False,
    dependencies=[Depends(require_internal_secret)],
)


class JobCompletion(BaseModel):
    """Body n8n sends when a workflow finishes."""

    result: dict[str, Any] | None = Field(
        default=None, description="Final workflow output. Omit on failure."
    )
    error: str | None = Field(
        default=None,
        description="Error message if the workflow failed. Omit on success.",
    )


async def _forward_partner_callback(url: str, payload: dict[str, Any]) -> None:
    """Fire-and-forget POST to a partner's callback URL. We never retry and
    never block the main flow on this; partners should treat callbacks as a
    convenience and still poll as the source of truth.
    """
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0)
        ) as client:
            resp = await client.post(url, json=payload)
            logger.info(
                "callback.sent url=%s status=%s",
                url,
                resp.status_code,
            )
    except httpx.HTTPError as exc:
        logger.warning("callback.failed url=%s error=%s", url, exc)


@router.post("/jobs/{job_id}/complete", status_code=status.HTTP_200_OK)
async def complete_job(
    job_id: UUID,
    body: JobCompletion,
    background: BackgroundTasks,
    repo: AirtableRepo = Depends(get_repo),
) -> dict[str, str]:
    existing = repo.get_job(job_id)
    if existing is None:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code=ErrorCode.JOB_NOT_FOUND,
            message="Unknown job.",
        )

    # Idempotency: if the job already reached a terminal state, return the
    # current record rather than overwriting. n8n sometimes retries.
    if existing.status in {JobStatus.DONE.value, JobStatus.FAILED.value}:
        logger.info(
            "complete.skip job_id=%s current_status=%s",
            job_id,
            existing.status,
        )
        return {"status": existing.status, "idempotent": "true"}

    if body.result is None and body.error is None:
        raise APIError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=ErrorCode.INVALID_REQUEST,
            message="Provide either 'result' or 'error'.",
        )

    updated = repo.complete_job(job_id, result=body.result, error=body.error)
    if updated is None:
        raise APIError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code=ErrorCode.INTERNAL_ERROR,
            message="Failed to record completion.",
        )

    # Fire the partner callback if configured. Fire-and-forget after response.
    if existing.callback_url:
        payload = {
            "job_id": str(job_id),
            "status": updated.status,
            "workflow_kind": updated.workflow_kind,
            # Mirror GET /v1/searches/{job_id}: surface the resolved company_id
            # so callback consumers see the same shape pollers do.
            "company_id": updated.request_payload.get("company_id"),
            "result": updated.result,
            "error": updated.error,
        }
        background.add_task(
            _forward_partner_callback, existing.callback_url, payload
        )

    logger.info(
        "complete.ok job_id=%s status=%s had_callback=%s",
        job_id,
        updated.status,
        bool(existing.callback_url),
    )
    return {"status": updated.status, "idempotent": "false"}


class DigestRunResult(BaseModel):
    """Per-run summary returned by the manual digest trigger."""

    companies: int
    sent: int
    skipped: int
    failed: int


@router.post("/digest/run", status_code=status.HTTP_200_OK)
async def run_digest(
    dry_run: bool = False,
    repo: AirtableRepo = Depends(get_repo),
    settings: Settings = Depends(get_settings),
) -> DigestRunResult:
    """Run the daily client-notification digest on demand (same work as the 15:00
    scheduler job) — for testing email delivery without waiting for the cron.

    ``?dry_run=true`` forces every email to ``email_from`` regardless of the global
    EMAIL_DRY_RUN setting; omitted, the global setting is honoured. Guarded by the
    internal shared secret via the router-level dependency.
    """
    service = NotificationService(repo, SmtpClient(settings), settings)
    summary = await service.run_daily(dry_run=dry_run or None)
    logger.info("digest.manual_run dry_run=%s summary=%s", dry_run, summary)
    return DigestRunResult(**summary)
