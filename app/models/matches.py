"""Pydantic models for the Search Matches endpoint.

These are read-only projections over the Airtable ``Search Matches`` table. The
gateway never writes to that table — it's owned by the n8n workflows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class MatchView(BaseModel):
    """A single Search Match row as exposed to partners.

    ``match`` and ``grant`` are both parsed JSON blobs coming from Airtable's
    ``Raw Json`` field (on Search Matches) and the ``Grant Details JSON`` lookup
    (pulled from the linked Grants row) respectively. If either blob fails to
    parse we surface ``_parse_error`` alongside the raw string so partners don't
    silently lose a row.
    """

    match_id: str | None = None
    created_at: datetime | None = None
    match: dict[str, Any]
    grant: dict[str, Any]


class MatchesView(BaseModel):
    """Response body for ``GET /v1/searches/{job_id}/matches``."""

    job_id: UUID
    count: int
    completed_at: datetime | None = None
    matches: list[MatchView]
