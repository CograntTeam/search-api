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
from fastapi import APIRouter, BackgroundTasks, Depends, Header, Response, status

from app.config import Settings, get_settings
from app.errors import APIError, ErrorCode, openapi_error_responses
from app.models.jobs import JobAccepted, JobCreate, JobStatus, JobView, WorkflowKind
from app.models.keys import ApiKey
from app.models.searches import DEFAULT_ORGANISATION_TYPE, SearchPayload
from app.repositories.airtable import AirtableRepo
from app.security import (
    bearer_scheme,
    enforce_search_creation_limit,
    get_repo,
    require_api_key,
)
from app.services.n8n import N8nClient

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/searches",
    tags=["searches"],
    # Advertise bearer auth in the OpenAPI schema so Swagger's "Authorize"
    # button covers every route on this router at once.
    dependencies=[Depends(bearer_scheme)],
)


def _pydantic_errors(exc: Exception) -> list[dict]:
    """Best-effort conversion of a Pydantic ``ValidationError`` (or
    anything with an ``errors()`` method) to a **JSON-safe** list of dicts
    for the error envelope. Pydantic stores the original exception inside
    ``ctx``, which isn't JSON-serialisable, so we keep only the fields we
    explicitly surface to partners.
    """
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return [{"msg": str(exc)}]
    try:
        raw = list(errors())  # type: ignore[misc]
    except Exception:  # noqa: BLE001
        return [{"msg": str(exc)}]
    safe: list[dict] = []
    for e in raw:
        item = {
            "loc": list(e.get("loc", ())),
            "type": e.get("type", ""),
            "msg": str(e.get("msg", "")),
        }
        if "input" in e and isinstance(e["input"], (str, int, float, bool, type(None))):
            item["input"] = e["input"]
        safe.append(item)
    return safe


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
    description=(
        "Kicks off an async grant search for the company specified in the "
        "request payload. Returns 202 with a ``job_id`` immediately — the "
        "search itself takes roughly 60–120 seconds.\n\n"
        "**Idempotency.** Pass an ``Idempotency-Key`` header to make the "
        "call safely retryable. A repeat with the same key returns the "
        "original job and sets ``Idempotency-Replayed: true``.\n\n"
        "**Example.**\n\n"
        "```bash\n"
        "curl -sS -X POST https://api.cogrant.eu/v1/searches \\\n"
        "  -H 'Authorization: Bearer cog_live_...' \\\n"
        "  -H 'Content-Type: application/json' \\\n"
        "  -H 'Idempotency-Key: 2a8e4c-sprint42' \\\n"
        "  -d '{\"payload\": {\"company_id\": \"recABCDEFGHIJKLMN\"}}'\n"
        "```"
    ),
    responses=openapi_error_responses(401, 422, 429),
)
async def create_search(
    body: JobCreate,
    background: BackgroundTasks,
    response: Response,
    # ``enforce_search_creation_limit`` depends on ``require_api_key``
    # and returns the same ApiKey, so this single dep gives us both auth
    # and the search-specific rate limit.
    api_key: ApiKey = Depends(enforce_search_creation_limit),
    repo: AirtableRepo = Depends(get_repo),
    settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobAccepted:
    # Short-circuit on a repeat: same partner + same Idempotency-Key returns
    # the original job instead of creating a new one. We echo the replay via
    # the ``Idempotency-Replayed`` header so partners can observe the cache
    # hit without comparing timestamps.
    if idempotency_key:
        existing = repo.find_job_by_idempotency(api_key.record_id, idempotency_key)
        if existing is not None:
            response.headers["Idempotency-Replayed"] = "true"
            return JobAccepted(
                job_id=existing.job_id,
                status=JobStatus(existing.status),
                created_at=existing.created_at,
            )
    # Validate the payload shape: either company_id OR the new-company
    # fields. Pydantic's ValidationError on this model is caught by our
    # handler and surfaced as a 422 INVALID_REQUEST envelope with
    # details.field_errors pointing at the offending field(s).
    if not isinstance(body.payload, dict) or not body.payload:
        raise APIError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=ErrorCode.INVALID_REQUEST,
            message="payload must be a non-empty JSON object.",
        )
    try:
        sp = SearchPayload.model_validate(body.payload)
    except Exception as exc:  # noqa: BLE001 — Pydantic ValidationError path
        raise APIError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=ErrorCode.INVALID_REQUEST,
            message="payload validation failed.",
            details={"field_errors": _pydantic_errors(exc)},
        ) from exc

    # New-company branch: create the Airtable row before firing the search.
    # If Airtable rejects the singleSelect (e.g. unknown country) we treat
    # it as partner input error and surface a 422; anything else is a 5xx.
    if sp.company_id is None:
        new_fields = sp.as_new_company_fields()
        try:
            created_company_id = repo.create_company(
                name=new_fields.company_name,
                description=new_fields.company_description,
                country=new_fields.country,
                website=new_fields.website,
                organisation_type=DEFAULT_ORGANISATION_TYPE,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "company.create_failed partner=%s", api_key.partner_name
            )
            # pyairtable surfaces HTTP 422 from Airtable as
            # ``HTTPError`` with response.status_code==422; we don't want
            # to leak internal details, but we do want the partner to
            # know it's their input (most commonly an unknown country).
            message = str(exc) if "422" in str(exc) else (
                "Failed to create company. Please try again or contact "
                "support if this persists."
            )
            raise APIError(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY if "422" in str(exc)
                else status.HTTP_502_BAD_GATEWAY,
                code=ErrorCode.INVALID_REQUEST if "422" in str(exc)
                else ErrorCode.INTERNAL_ERROR,
                message=message,
            ) from exc
        resolved_company_id = created_company_id
        logger.info(
            "search.company_created partner=%s company_id=%s",
            api_key.partner_name,
            created_company_id,
        )
    else:
        resolved_company_id = sp.company_id

    # Always forward a concrete company_id to n8n; strip any new-company
    # fields so the downstream workflow never sees them.
    forwarded = sp.forwarded_payload(company_id=resolved_company_id)

    job_id = uuid4()
    job = repo.create_job(
        job_id=job_id,
        api_key_record_id=api_key.record_id,
        workflow_kind=WorkflowKind.SEARCH,
        request_payload=forwarded,
        callback_url=body.callback_url,
        idempotency_key=idempotency_key,
    )
    background.add_task(_dispatch_search, job_id, forwarded, settings, repo)
    logger.info(
        "search.created job_id=%s partner=%s company_id=%s",
        job_id,
        api_key.partner_name,
        resolved_company_id,
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
    description=(
        "Polls the job. ``status`` transitions ``queued`` → ``running`` → "
        "``done`` or ``failed``. The full result set lives under "
        "``GET /v1/searches/{job_id}/matches`` — this endpoint stays "
        "lightweight for frequent polling.\n\n"
        "A reasonable polling cadence is every 5–10 seconds; searches "
        "typically complete within 60–120 seconds."
    ),
    responses=openapi_error_responses(401, 404, 429),
)
async def get_search(
    job_id: UUID,
    api_key: ApiKey = Depends(require_api_key),
    repo: AirtableRepo = Depends(get_repo),
) -> JobView:
    job = repo.get_job(job_id)
    if job is None:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code=ErrorCode.JOB_NOT_FOUND,
            message="Job not found.",
        )
    # Enforce that partners can only see their own jobs.
    if job.api_key_record_id != api_key.record_id:
        # Return 404 instead of 403 to avoid leaking job ID existence.
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code=ErrorCode.JOB_NOT_FOUND,
            message="Job not found.",
        )
    if job.workflow_kind != WorkflowKind.SEARCH.value:
        # Wrong endpoint for this job kind — still 404 from the partner's view.
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code=ErrorCode.JOB_NOT_FOUND,
            message="Job not found.",
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
