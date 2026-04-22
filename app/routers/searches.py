"""Public search endpoints.

    POST /v1/searches           -> create an async search job
    GET  /v1/searches/{job_id}  -> poll a search job

Both endpoints require a valid partner API key.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status

from app.config import Settings, get_settings
from app.models.jobs import JobAccepted, JobCreate, JobStatus, JobView, WorkflowKind
from app.models.keys import ApiKey
from app.repositories.airtable import AirtableRepo
from app.security import get_repo, require_api_key
from app.services.n8n import N8nClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/searches", tags=["searches"])


# ---------------------------------------------------------------------------
# Background task: fire n8n, mark job running or failed.
# ---------------------------------------------------------------------------
async def _dispatch_search(
    job_id: UUID, payload: dict, settings: Settings, repo: AirtableRepo
) -> None:
    """Runs after the response is returned. Any exception ends up in the
    job's ``error`` field, so partners see a proper failure instead of a
    hung ``queued`` job.

    The caller passes the request-scoped repo (pyairtable's Table is thread-
    safe), which also lets tests inject a fake without patching.
    """
    client = N8nClient(settings)
    try:
        execution_id = await client.fire_search(job_id=job_id, payload=payload)
        repo.set_job_running(job_id, n8n_execution_id=execution_id)
        logger.info(
            "search.dispatched job_id=%s execution_id=%s", job_id, execution_id
        )
    except httpx.HTTPError as exc:
        logger.exception("search.dispatch_failed job_id=%s", job_id)
        repo.complete_job(
            job_id,
            error=f"Failed to reach search workflow: {exc.__class__.__name__}",
        )
    except Exception:  # noqa: BLE001
        logger.exception("search.dispatch_unexpected job_id=%s", job_id)
        repo.complete_job(job_id, error="Internal dispatch error.")


# ---------------------------------------------------------------------------
# POST /v1/searches
# ---------------------------------------------------------------------------
@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAccepted,
    summary="Create a new grant-search job",
)
async def create_search(
    body: JobCreate,
    background: BackgroundTasks,
    api_key: ApiKey = Depends(require_api_key),
    repo: AirtableRepo = Depends(get_repo),
    settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobAccepted:
    # Short-circuit on a repeat: same partner + same Idempotency-Key returns
    # the original job instead of creating a new one.
    if idempotency_key:
        existing = repo.find_job_by_idempotency(api_key.record_id, idempotency_key)
        if existing is not None:
            return JobAccepted(
                job_id=existing.job_id,
                status=JobStatus(existing.status),
                created_at=existing.created_at,
            )
    # Minimal payload sanity check — the real shape validation lives in n8n
    # because the search contract may evolve faster than the gateway. We only
    # require the body be a non-empty dict.
    if not isinstance(body.payload, dict) or not body.payload:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="payload must be a non-empty JSON object.",
        )

    job_id = uuid4()
    job = repo.create_job(
        job_id=job_id,
        api_key_record_id=api_key.record_id,
        workflow_kind=WorkflowKind.SEARCH,
        request_payload=body.payload,
        callback_url=body.callback_url,
        idempotency_key=idempotency_key,
    )
    background.add_task(_dispatch_search, job_id, body.payload, settings, repo)
    logger.info(
        "search.created job_id=%s partner=%s", job_id, api_key.partner_name
    )
    return JobAccepted(
        job_id=job.job_id,
        status=JobStatus(job.status),
        created_at=job.created_at,
    )


# ---------------------------------------------------------------------------
# GET /v1/searches/{job_id}
# ---------------------------------------------------------------------------
@router.get(
    "/{job_id}",
    response_model=JobView,
    summary="Fetch the current state of a search job",
)
async def get_search(
    job_id: UUID,
    api_key: ApiKey = Depends(require_api_key),
    repo: AirtableRepo = Depends(get_repo),
) -> JobView:
    job = repo.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found."
        )
    # Enforce that partners can only see their own jobs.
    if job.api_key_record_id != api_key.record_id:
        # Return 404 instead of 403 to avoid leaking job ID existence.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found."
        )
    if job.workflow_kind != WorkflowKind.SEARCH.value:
        # Wrong endpoint for this job kind — still 404 from the partner's view.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found."
        )
    return JobView(
        job_id=job.job_id,
        status=JobStatus(job.status),
        workflow_kind=WorkflowKind(job.workflow_kind),
        result=job.result,
        error=job.error,
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
    )
