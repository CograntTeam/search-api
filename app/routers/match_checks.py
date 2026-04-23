"""Public match-check endpoints.

    POST /v1/match-checks/a           -> create an async match-check A job
    GET  /v1/match-checks/a/{job_id}  -> poll a match-check A job
    POST /v1/match-checks/b           -> create an async match-check B job
    GET  /v1/match-checks/b/{job_id}  -> poll a match-check B job

The two variants map to n8n workflows 1.1A and 1.1B. They share the exact
same job-row model as /v1/searches; only the ``workflow_kind`` discriminator
changes. Both require a valid partner API key.
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

router = APIRouter(prefix="/v1/match-checks", tags=["match-checks"])


# ---------------------------------------------------------------------------
# Background task factory: fire n8n, mark job running or failed.
# ---------------------------------------------------------------------------
def _make_dispatcher(workflow_kind: WorkflowKind):
    """Build a background-task coroutine for a given match-check variant.

    Each variant posts to a different n8n webhook but shares the same error
    handling + job-row transitions, so we factor it here.
    """
    log_prefix = workflow_kind.value  # "match_check_a" / "match_check_b"

    async def _dispatch(
        job_id: UUID, payload: dict, settings: Settings, repo: AirtableRepo
    ) -> None:
        """Runs after the 202 response is returned. Any exception ends up in
        the job's ``error`` field, so partners see a proper failure instead of
        a hung ``queued`` job.
        """
        client = N8nClient(settings)
        try:
            if workflow_kind is WorkflowKind.MATCH_CHECK_A:
                execution_id = await client.fire_match_check_a(
                    job_id=job_id, payload=payload
                )
            elif workflow_kind is WorkflowKind.MATCH_CHECK_B:
                execution_id = await client.fire_match_check_b(
                    job_id=job_id, payload=payload
                )
            else:  # pragma: no cover - defensive
                raise RuntimeError(f"Unsupported workflow_kind: {workflow_kind}")
            repo.set_job_running(job_id, n8n_execution_id=execution_id)
            logger.info(
                "%s.dispatched job_id=%s execution_id=%s",
                log_prefix,
                job_id,
                execution_id,
            )
        except httpx.HTTPError as exc:
            logger.exception("%s.dispatch_failed job_id=%s", log_prefix, job_id)
            repo.complete_job(
                job_id,
                error=f"Failed to reach {log_prefix} workflow: {exc.__class__.__name__}",
            )
        except Exception:  # noqa: BLE001
            logger.exception("%s.dispatch_unexpected job_id=%s", log_prefix, job_id)
            repo.complete_job(job_id, error="Internal dispatch error.")

    return _dispatch


_dispatch_match_check_a = _make_dispatcher(WorkflowKind.MATCH_CHECK_A)
_dispatch_match_check_b = _make_dispatcher(WorkflowKind.MATCH_CHECK_B)


# ---------------------------------------------------------------------------
# Shared handler helpers.
# ---------------------------------------------------------------------------
def _create_job(
    *,
    workflow_kind: WorkflowKind,
    dispatcher,
    body: JobCreate,
    background: BackgroundTasks,
    api_key: ApiKey,
    repo: AirtableRepo,
    settings: Settings,
    idempotency_key: str | None,
) -> JobAccepted:
    # Short-circuit on a repeat: same partner + same Idempotency-Key returns
    # the original job instead of creating a new one.
    if idempotency_key:
        existing = repo.find_job_by_idempotency(api_key.record_id, idempotency_key)
        if existing is not None:
            # Only collapse repeats within the same workflow kind; a replay on
            # a different kind would otherwise return the wrong job shape.
            if existing.workflow_kind == workflow_kind.value:
                return JobAccepted(
                    job_id=existing.job_id,
                    status=JobStatus(existing.status),
                    created_at=existing.created_at,
                )

    # Minimal payload sanity check — shape validation lives in n8n.
    if not isinstance(body.payload, dict) or not body.payload:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="payload must be a non-empty JSON object.",
        )

    job_id = uuid4()
    job = repo.create_job(
        job_id=job_id,
        api_key_record_id=api_key.record_id,
        workflow_kind=workflow_kind,
        request_payload=body.payload,
        callback_url=body.callback_url,
        idempotency_key=idempotency_key,
    )
    background.add_task(dispatcher, job_id, body.payload, settings, repo)
    logger.info(
        "%s.created job_id=%s partner=%s",
        workflow_kind.value,
        job_id,
        api_key.partner_name,
    )
    return JobAccepted(
        job_id=job.job_id,
        status=JobStatus(job.status),
        created_at=job.created_at,
    )


def _get_job(
    *,
    workflow_kind: WorkflowKind,
    job_id: UUID,
    api_key: ApiKey,
    repo: AirtableRepo,
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
    if job.workflow_kind != workflow_kind.value:
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


# ---------------------------------------------------------------------------
# Match-check A
# ---------------------------------------------------------------------------
@router.post(
    "/a",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAccepted,
    summary="Create a new match-check A job",
)
async def create_match_check_a(
    body: JobCreate,
    background: BackgroundTasks,
    api_key: ApiKey = Depends(require_api_key),
    repo: AirtableRepo = Depends(get_repo),
    settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobAccepted:
    return _create_job(
        workflow_kind=WorkflowKind.MATCH_CHECK_A,
        dispatcher=_dispatch_match_check_a,
        body=body,
        background=background,
        api_key=api_key,
        repo=repo,
        settings=settings,
        idempotency_key=idempotency_key,
    )


@router.get(
    "/a/{job_id}",
    response_model=JobView,
    summary="Fetch the current state of a match-check A job",
)
async def get_match_check_a(
    job_id: UUID,
    api_key: ApiKey = Depends(require_api_key),
    repo: AirtableRepo = Depends(get_repo),
) -> JobView:
    return _get_job(
        workflow_kind=WorkflowKind.MATCH_CHECK_A,
        job_id=job_id,
        api_key=api_key,
        repo=repo,
    )


# ---------------------------------------------------------------------------
# Match-check B
# ---------------------------------------------------------------------------
@router.post(
    "/b",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAccepted,
    summary="Create a new match-check B job",
)
async def create_match_check_b(
    body: JobCreate,
    background: BackgroundTasks,
    api_key: ApiKey = Depends(require_api_key),
    repo: AirtableRepo = Depends(get_repo),
    settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobAccepted:
    return _create_job(
        workflow_kind=WorkflowKind.MATCH_CHECK_B,
        dispatcher=_dispatch_match_check_b,
        body=body,
        background=background,
        api_key=api_key,
        repo=repo,
        settings=settings,
        idempotency_key=idempotency_key,
    )


@router.get(
    "/b/{job_id}",
    response_model=JobView,
    summary="Fetch the current state of a match-check B job",
)
async def get_match_check_b(
    job_id: UUID,
    api_key: ApiKey = Depends(require_api_key),
    repo: AirtableRepo = Depends(get_repo),
) -> JobView:
    return _get_job(
        workflow_kind=WorkflowKind.MATCH_CHECK_B,
        job_id=job_id,
        api_key=api_key,
        repo=repo,
    )
