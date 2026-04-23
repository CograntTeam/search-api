"""Pydantic models for the Search Matches endpoint.

These are read-only projections over the Airtable ``Search Matches`` table. The
gateway never writes to that table — it's owned by the n8n workflows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.grant_details import GrantDetails
from app.models.match_details import MatchDetails


class MatchView(BaseModel):
    """A single Search Match row as exposed to partners.

    ``match`` is the decision block produced by the Grant–Company Match
    Sanity Checker agent (parsed from the ``Raw Json`` field on the
    Search Matches row).

    ``grant`` is the structured grant metadata (parsed from the
    ``Grant Details JSON`` lookup on the linked Grants row).

    Both are typed but use ``extra="allow"`` everywhere, so evolving
    upstream shapes never break a response and unknown fields are
    preserved verbatim. See :mod:`app.models.match_details` and
    :mod:`app.models.grant_details` for per-field descriptions.
    """

    # ``populate_by_name`` + ``by_alias=True`` on the response makes the
    # Title Case ``match`` keys round-trip untouched.
    model_config = ConfigDict(populate_by_name=True)

    match_id: str | None = None
    created_at: datetime | None = None
    match: MatchDetails | dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Analyst-style decision block attached to this match — see "
            "``MatchDetails`` schema for field-by-field documentation."
        ),
    )
    grant: GrantDetails | dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Structured grant metadata — see ``GrantDetails`` schema for "
            "field-by-field documentation."
        ),
    )


class MatchesView(BaseModel):
    """Response body for ``GET /v1/searches/{job_id}/matches``."""

    model_config = ConfigDict(populate_by_name=True)

    job_id: UUID
    count: int
    completed_at: datetime | None = None
    matches: list[MatchView]
