"""Airtable repository layer.

Thin wrapper around ``pyairtable`` that we use as the current job+key store.
Everything that touches Airtable goes through here so that migration to
Postgres (when volume grows) is a one-file swap.

Note on concurrency: pyairtable's ``Table`` is thread-safe for reads and
writes, but Airtable itself enforces 5 req/s per base. We deliberately make
one call per operation and rely on FastAPI's async runloop to keep latency
down; do not introduce fan-out patterns here.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pyairtable import Api

from app.config import Settings, get_settings
from app.models.jobs import Job, JobStatus, WorkflowKind
from app.models.keys import ApiKey, KeyStatus

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _dumps(v: Any) -> str:
    """Airtable multilineText can only hold strings; JSON-serialise dicts."""
    return json.dumps(v, default=str) if v is not None else ""


def _loads(v: str | None) -> dict[str, Any] | None:
    if not v:
        return None
    try:
        return json.loads(v)
    except json.JSONDecodeError:
        logger.warning("Airtable field was not valid JSON; returning raw string")
        return {"_raw": v}


def _chunks(items: list[str], size: int) -> list[list[str]]:
    """Split a list of record IDs into batches for OR(RECORD_ID()=...) queries."""
    return [items[i : i + size] for i in range(0, len(items), size)]


class AirtableRepo:
    """Single entry point for all Airtable reads/writes in the gateway."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._api = Api(self.settings.airtable_pat)
        self._keys = self._api.table(
            self.settings.airtable_base_id,
            self.settings.airtable_api_keys_table_id,
        )
        self._jobs = self._api.table(
            self.settings.airtable_base_id,
            self.settings.airtable_api_jobs_table_id,
        )
        self._search_matches = self._api.table(
            self.settings.airtable_base_id,
            self.settings.airtable_search_matches_table_id,
        )
        self._companies = self._api.table(
            self.settings.airtable_base_id,
            self.settings.airtable_companies_table_id,
        )
        # Optional: the partner API and the daily-digest job build the repo too and
        # don't touch grants, so build it only when configured. The reverse-search
        # poller — the sole consumer — is gated on this id in start_scheduler.
        gid = self.settings.airtable_grants_table_id
        self._grants = (
            self._api.table(self.settings.airtable_base_id, gid) if gid else None
        )

    # ------------------------------------------------------------------
    # Companies
    # ------------------------------------------------------------------
    # Field IDs are hardcoded by design. These are write-only targets used
    # by the new-company branch of POST /v1/searches; keeping them as IDs
    # (not names) insulates the gateway from Airtable field renames.
    _COMPANIES_NAME = "fldv3wHfnpt8qeRoM"
    _COMPANIES_DESCRIPTION = "fld4QgZu6it7VmZyY"
    _COMPANIES_COUNTRY = "fldkplI5JzXUkw6V0"
    _COMPANIES_WEBSITE = "fldiX92NcEy8JiOLo"
    _COMPANIES_ORG_TYPE = "fldYLkD9P3nBcJbXH"
    _COMPANIES_LEAD_SOURCE = "fldcgpMLIduhp4QBO"

    def create_company(
        self,
        *,
        name: str,
        description: str,
        country: str,
        website: str | None = None,
        organisation_type: str = "Private Business",
        lead_source: str = "Search API",
    ) -> str:
        """Create a Companies row and return its record ID.

        Partner-supplied fields are passed through. ``organisation_type``
        defaults to ``"Private Business"`` because the self-serve API is
        scoped to that category; other types are onboarded manually.
        ``lead_source`` defaults to ``"Search API"`` so sales can tell these
        self-serve rows apart from other inbound leads.

        Raises :class:`pyairtable.api.types.PyAirtableError` (or plain
        ``requests`` errors) on Airtable failures — let them bubble up to
        the router so we emit a 5xx envelope. We pass ``typecast=True``
        so the ``Country``, ``Organisation type`` and ``Lead source``
        singleSelect fields accept string values; Airtable will 422 if the
        string isn't a known option, which we translate to 422 upstream.
        ``Lead source`` is gateway-controlled, so its option already exists.
        """
        fields: dict[str, Any] = {
            self._COMPANIES_NAME: name,
            self._COMPANIES_DESCRIPTION: description,
            self._COMPANIES_COUNTRY: country,
            self._COMPANIES_ORG_TYPE: organisation_type,
            self._COMPANIES_LEAD_SOURCE: lead_source,
        }
        if website:
            fields[self._COMPANIES_WEBSITE] = website
        rec = self._companies.create(fields, typecast=True)
        logger.info(
            "company.created record_id=%s country=%s type=%s",
            rec["id"],
            country,
            organisation_type,
        )
        return rec["id"]

    # ------------------------------------------------------------------
    # api_keys
    # ------------------------------------------------------------------
    def find_key_by_hash(self, key_hash: str) -> ApiKey | None:
        """Look up an active or revoked key by its SHA-256 hash.

        Returns None if not found. Callers must check ``.status``.
        """
        formula = f"{{key_hash}} = '{key_hash}'"
        records = self._keys.all(formula=formula, max_records=1)
        if not records:
            return None
        return self._row_to_key(records[0])

    def touch_key_last_used(self, record_id: str) -> None:
        """Update last_used_at. Fire-and-forget; swallow errors."""
        try:
            self._keys.update(record_id, {"last_used_at": _utc_now_iso()})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to update last_used_at for %s: %s", record_id, exc)

    @staticmethod
    def _row_to_key(rec: dict[str, Any]) -> ApiKey:
        f = rec["fields"]

        def _optional_int(raw: Any) -> int | None:
            """Airtable number cells come back as int/float; empty cells are
            absent entirely. We treat absent/blank/<=0 as "no cap"."""
            if raw is None or raw == "":
                return None
            try:
                val = int(raw)
            except (TypeError, ValueError):
                return None
            return val if val > 0 else None

        return ApiKey(
            record_id=rec["id"],
            partner_name=f.get("partner_name", ""),
            key_hash=f.get("key_hash", ""),
            key_prefix=f.get("key_prefix"),
            status=KeyStatus(f.get("status", "active")),
            rate_limit_per_min=_optional_int(f.get("rate_limit_per_min")) or 60,
            rate_limit_per_day=_optional_int(f.get("rate_limit_per_day")),
            rate_limit_per_week=_optional_int(f.get("rate_limit_per_week")),
            searches_per_day=_optional_int(f.get("searches_per_day")),
            searches_per_week=_optional_int(f.get("searches_per_week")),
            contact_email=f.get("contact_email"),
            created_at=f.get("created_at"),
            last_used_at=f.get("last_used_at"),
        )

    # ------------------------------------------------------------------
    # api_jobs
    # ------------------------------------------------------------------
    def create_job(
        self,
        *,
        job_id: UUID,
        api_key_record_id: str,
        workflow_kind: WorkflowKind,
        request_payload: dict[str, Any],
        callback_url: str | None = None,
        idempotency_key: str | None = None,
    ) -> Job:
        now = _utc_now_iso()
        fields: dict[str, Any] = {
            "job_id": str(job_id),
            "api_key": [api_key_record_id],
            "workflow_kind": workflow_kind.value,
            "status": JobStatus.QUEUED.value,
            "request_payload": _dumps(request_payload),
            "created_at": now,
            "updated_at": now,
        }
        if callback_url:
            fields["callback_url"] = callback_url
        if idempotency_key:
            fields["idempotency_key"] = idempotency_key

        rec = self._jobs.create(fields)
        return self._row_to_job(rec)

    def get_job(self, job_id: UUID) -> Job | None:
        formula = f"{{job_id}} = '{job_id}'"
        records = self._jobs.all(formula=formula, max_records=1)
        if not records:
            return None
        return self._row_to_job(records[0])

    def find_job_by_idempotency(
        self, api_key_record_id: str, idempotency_key: str
    ) -> Job | None:
        formula = (
            "AND("
            f"{{idempotency_key}} = '{idempotency_key}', "
            f"FIND('{api_key_record_id}', ARRAYJOIN({{api_key}})) > 0"
            ")"
        )
        records = self._jobs.all(formula=formula, max_records=1)
        if not records:
            return None
        return self._row_to_job(records[0])

    def set_job_running(
        self, job_id: UUID, n8n_execution_id: str | None = None
    ) -> None:
        record_id = self._job_record_id(job_id)
        if not record_id:
            return
        fields: dict[str, Any] = {"status": JobStatus.RUNNING.value, "updated_at": _utc_now_iso()}
        if n8n_execution_id:
            fields["n8n_execution_id"] = n8n_execution_id
        self._jobs.update(record_id, fields)

    def complete_job(
        self,
        job_id: UUID,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> Job | None:
        record_id = self._job_record_id(job_id)
        if not record_id:
            return None
        now = _utc_now_iso()
        status = JobStatus.FAILED if error else JobStatus.DONE
        fields: dict[str, Any] = {
            "status": status.value,
            "updated_at": now,
            "completed_at": now,
        }
        if result is not None:
            fields["result"] = _dumps(result)
        if error:
            fields["error"] = error
        rec = self._jobs.update(record_id, fields)
        return self._row_to_job(rec)

    # ------------------------------------------------------------------
    # Search Matches (read-only; owned by the n8n workflows)
    # ------------------------------------------------------------------
    def list_matches_for_job(self, api_job_id: UUID) -> list[dict[str, Any]]:
        """Return all Search Match rows whose ``api_job_id`` equals the given
        UUID. Includes only the fields the gateway exposes to partners.

        pyairtable's ``.all()`` auto-paginates server-side (100 rows per page),
        so this is a single logical call from the gateway's perspective even
        when a run produces hundreds of matches. Order is by Airtable's native
        record order; callers that need a stable ordering should sort client-
        side on ``Match Id`` or ``Created Time``.
        """
        # UUIDs contain only hex + dashes, so they're safe inside a single-
        # quoted Airtable formula literal. We still lean on pyairtable for
        # encoding, rather than f-stringing arbitrary values.
        formula = f"{{api_job_id}} = '{api_job_id}'"
        records = self._search_matches.all(
            formula=formula,
            fields=[
                "api_job_id",
                "Raw Json",
                "Grant Details JSON",
                "Match Id",
                "Created Time",
            ],
        )
        return records

    # ------------------------------------------------------------------
    # Reverse search — Grants (read/write; the in-process scheduler owns these)
    # ------------------------------------------------------------------
    # Field names (not IDs) are used across the reverse-search/notification
    # methods on purpose: the write-back mirrors the n8n source-of-truth mapping
    # one-for-one, so name-keyed dicts read exactly like the workflow.
    _GRANT_FIELDS = [
        "Name",
        "Grant Description",
        "Grant Details",
        "Reverse Search Status",
        "Scrape Status",
        "Application Deadline",
        "Grant Geography",
        "Eligible Country",
        "Eligible City",
        "Eligible Types of Organisation",
        "Supported Sector",
        "Application Areas",
        "Size Classes",
        "Minimum Years Established",
        "Maximum Years Established",
        "Support Forms",
        "Organisation Archetypes",
        "Required Subject Expertise",
        "Consortium Possibility",
        "Search Matches",  # reverse link of Search Matches.Grant — existing matches
    ]

    def list_queued_grants(self) -> list[dict[str, Any]]:
        """Grants explicitly queued for a reverse search. Enrichment/deadline
        preconditions are checked in Python so the funnel log can explain any skip."""
        return self._grants.all(
            formula="{Reverse Search Status} = 'Queued'",
            fields=self._GRANT_FIELDS,
        )

    def list_in_progress_grants(self) -> list[dict[str, Any]]:
        """Grants stranded mid-run. Status flips to In Progress *before* a run and a
        completion log is always written at the end, so an In Progress grant is an
        orphan from a killed process — re-queued on startup (see requeue_orphans)."""
        return self._grants.all(
            formula="{Reverse Search Status} = 'In Progress'",
            fields=self._GRANT_FIELDS,
        )

    def update_grant_reverse_search(
        self, grant_record_id: str, *, status: str, log: str | None = None
    ) -> None:
        fields: dict[str, Any] = {"Reverse Search Status": status}
        if log is not None:
            fields["Reverse Search Log"] = log
        self._grants.update(grant_record_id, fields, typecast=True)

    # ------------------------------------------------------------------
    # Reverse search — Companies (read all candidates for filtering)
    # ------------------------------------------------------------------
    _COMPANY_FILTER_FIELDS = [
        "Company name",
        "Notification Customer",  # reverse-search Pro gate (see match_filters)
        "Country",
        "City of Establishment",
        "Organisation Type",
        "Activity Sectors",
        "Application Area",
        "Company Size Class",
        "Years of Establishment",
        "Acceptable Instruments",
        "Organisation Archetype",
        "Subject Expertise",
        "Consortium Stance",
        "Company description",
        "Company Preference Profile",
    ]

    def list_companies_for_filtering(self) -> list[dict[str, Any]]:
        """All companies with just the fields the filters + LLM input need.
        Cache the result across a single poll cycle; companies change slowly."""
        return self._companies.all(fields=self._COMPANY_FILTER_FIELDS)

    # ------------------------------------------------------------------
    # Reverse search — Search Matches (write + (grant, company) dedup)
    # ------------------------------------------------------------------
    def create_search_match(self, fields: dict[str, Any]) -> str:
        rec = self._search_matches.create(fields, typecast=True)
        return rec["id"]

    def company_ids_with_existing_matches(self, match_ids: list[str]) -> set[str]:
        """Company record IDs already linked by the given Search Match rows.

        We pass a grant's existing matches (its ``Search Matches`` reverse link)
        and get back the companies they cover, so a re-run skips (grant, company)
        pairs that already have a match instead of duplicating + re-notifying.
        """
        out: set[str] = set()
        for chunk in _chunks(match_ids, 50):
            formula = "OR(" + ",".join(f"RECORD_ID()='{m}'" for m in chunk) + ")"
            for rec in self._search_matches.all(formula=formula, fields=["Company"]):
                for cid in rec.get("fields", {}).get("Company", []) or []:
                    out.add(cid)
        return out

    # ------------------------------------------------------------------
    # Daily client-notification digest
    # ------------------------------------------------------------------
    def list_pending_notification_matches(self) -> list[dict[str, Any]]:
        """Reverse-search matches awaiting their daily email."""
        return self._search_matches.all(
            formula="{Notification Status} = 'Pending'",
            fields=[
                "Company",
                "Name",
                "Match Description",
                "Raw Json",
                "Grant",
                "Type",  # Quick Win / Strategic Bid / Stretch Fit (digest eyebrow)
                "Grant Details JSON",  # funding, deadline, agency, programme, geography
            ],
        )

    def get_companies_by_ids(self, company_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Map company record ID -> notification-relevant fields."""
        out: dict[str, dict[str, Any]] = {}
        for chunk in _chunks(company_ids, 50):
            formula = "OR(" + ",".join(f"RECORD_ID()='{c}'" for c in chunk) + ")"
            for rec in self._companies.all(
                formula=formula,
                fields=[
                    "Company name",
                    "Email",
                    "Notification Customer",
                    "First name (šauksmininkas)",  # vocative first name for the greeting
                ],
            ):
                out[rec["id"]] = rec.get("fields", {})
        return out

    def set_match_notification_status(self, match_id: str, status: str) -> None:
        self._search_matches.update(
            match_id, {"Notification Status": status}, typecast=True
        )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _job_record_id(self, job_id: UUID) -> str | None:
        formula = f"{{job_id}} = '{job_id}'"
        records = self._jobs.all(
            formula=formula, max_records=1, fields=["job_id"]
        )
        return records[0]["id"] if records else None

    @staticmethod
    def _row_to_job(rec: dict[str, Any]) -> Job:
        f = rec["fields"]
        linked = f.get("api_key") or []
        return Job(
            job_id=UUID(f["job_id"]),
            api_key_record_id=linked[0] if linked else None,
            workflow_kind=WorkflowKind(f.get("workflow_kind", "search")),
            status=JobStatus(f.get("status", "queued")),
            request_payload=_loads(f.get("request_payload")) or {},
            result=_loads(f.get("result")),
            error=f.get("error"),
            callback_url=f.get("callback_url"),
            idempotency_key=f.get("idempotency_key"),
            n8n_execution_id=f.get("n8n_execution_id"),
            created_at=f.get("created_at"),
            updated_at=f.get("updated_at"),
            completed_at=f.get("completed_at"),
        )
