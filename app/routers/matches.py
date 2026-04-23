"""Search-match results endpoint.

    GET /v1/searches/{job_id}/matches

Returns the full, merged match-and-grant dataset for a completed search job.
Lives on a separate endpoint from ``GET /v1/searches/{job_id}`` because the
payload is heavy (parsed match JSON + full grant-details JSON per row) and we
want to keep status polls cheap.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.models.jobs import JobStatus, WorkflowKind
from app.models.keys import ApiKey
from app.models.matches import MatchesView, MatchView
from app.repositories.airtable import AirtableRepo
from app.security import get_repo, require_api_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/searches", tags=["searches"])


def _parse_json_field(value: Any) -> dict[str, Any]:
    """Parse a stringified-JSON Airtable value into a dict.

    Handles three shapes Airtable actually returns:
      * ``str``                -> direct JSON string (multilineText field)
      * ``list[str]`` length 1 -> lookup field pointing at one linked record
      * ``None`` / empty       -> empty dict

    On any parse failure we DON'T drop the row silently; instead we surface
    the raw value and the error so partners can flag it back to us.
    """
    if value is None or value == "" or value == []:
        return {}

    if isinstance(value, list):
        if not value:
            return {}
        # Multi-record lookups return a list; in practice each Search Match
        # row links to exactly one Grant, so we take the first element.
        value = value[0]

    if not isinstance(value, str):
        # Airtable sometimes hands us an already-parsed object if the field
        # type changes; pass it through.
        return value if isinstance(value, dict) else {"_raw": value}

    try:
        parsed = json.loads(value)
    except (ValueError, TypeError) as exc:
        return {"_parse_error": str(exc), "_raw": value}

    if not isinstance(parsed, dict):
        # Defensive: if the JSON is e.g. a list or a scalar, wrap it so the
        # response stays structurally consistent.
        return {"_raw_parsed": parsed}
    return parsed


def _row_to_match_view(rec: dict[str, Any]) -> MatchView:
    f = rec.get("fields", {})
    raw = f.get("Raw Json")
    grant_details = f.get("Grant Details JSON")

    created_raw = f.get("Created Time")
    created_at: datetime | None = None
    if isinstance(created_raw, str) and created_raw:
        try:
            # Airtable createdTime fields are ISO-8601 with trailing "Z".
            created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        except ValueError:
            created_at = None

    match_id_raw = f.get("Match Id")
    # formula fields can return as a string or sometimes wrapped; coerce.
    match_id = str(match_id_raw) if match_id_raw is not None else None

    return MatchView(
        match_id=match_id,
        created_at=created_at,
        match=_parse_json_field(raw),
        grant=_parse_json_field(grant_details),
    )


@router.get(
    "/{job_id}/matches",
    response_model=MatchesView,
    summary="Fetch the full list of matches for a completed search job",
)
async def get_search_matches(
    job_id: UUID,
    api_key: ApiKey = Depends(require_api_key),
    repo: AirtableRepo = Depends(get_repo),
) -> MatchesView:
    job = repo.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found."
        )
    # Partners only see their own jobs. 404 instead of 403 avoids leaking
    # existence of other partners' job IDs.
    if job.api_key_record_id != api_key.record_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found."
        )
    if job.workflow_kind != WorkflowKind.SEARCH.value:
        # Wrong endpoint for this job kind — still 404 from the partner's view.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found."
        )

    # Matches only exist once the run has finished. If we're still running,
    # tell the partner to keep polling status. If we failed, echo the error.
    if job.status == JobStatus.FAILED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "detail": "Job failed; no matches available.",
                "status": job.status,
                "error": job.error,
            },
        )
    if job.status != JobStatus.DONE.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "detail": "Job not yet complete.",
                "status": job.status,
                "hint": f"Poll GET /v1/searches/{job_id} until status=done.",
            },
        )

    rows = repo.list_matches_for_job(job_id)
    matches = [_row_to_match_view(r) for r in rows]
    logger.info(
        "matches.list job_id=%s partner=%s count=%d",
        job_id,
        api_key.partner_name,
        len(matches),
    )
    return MatchesView(
        job_id=job.job_id,
        count=len(matches),
        completed_at=job.completed_at,
        matches=matches,
    )
