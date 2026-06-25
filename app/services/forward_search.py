"""Forward (API) grant search — one company → eligible grants → Search Matches.

In-process port of the n8n "API Search" workflow, and the mirror image of
:class:`app.services.reverse_search.ReverseSearchService` (which goes grant →
companies). Driven immediately from ``POST /v1/searches``' background task:

1. load the company; mark it ``Search in Progress``,
2. classify it (verbatim Gemini prompt) and write the classification back if it
   isn't already classified,
3. filter the enriched Grants table down with :func:`run_company_funnel`,
4. drop grants the company already has a match for (dedup),
5. Gemini-sanity-check each remaining grant (bounded concurrency) and create a
   Search Match per PASS, linked to ``api_job_id``,
6. mark the company ``Search Completed`` and return a summary (the job result).

Airtable writes are paced to stay under the 5 req/s per-base ceiling, exactly as
the reverse search does.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections import Counter
from datetime import UTC, date, datetime
from typing import Any

from app.config import Settings
from app.middleware import current_request_id
from app.models.company_classification import CompanyClassification
from app.repositories.airtable import AirtableRepo
from app.services.gemini_client import GeminiClient, GeminiError
from app.services.match_filters import run_company_funnel
from app.services.match_mapping import build_search_match_fields, evaluate_gate
from app.services.reverse_search import (
    _WRITE_PACING_SECONDS,
    _build_company_description,
    _build_grant_description,
    _text,
)

logger = logging.getLogger(__name__)

# A company is "already classified" when these are all populated (mirrors the
# n8n "Already Classified?" IF node).
_CLASSIFICATION_FIELDS = (
    "Activity Sectors",
    "Application Area",
    "Company Size Class",
    "Acceptable Instruments",
)
# Forward-search matches are partner-facing, not for the daily client digest, so
# they are parked at "Idle" rather than "Pending" (which the digest pulls).
_MATCH_NOTIFICATION_STATUS = "Idle"


def _present(value: Any) -> bool:
    """True if an Airtable cell carries a value (not None / "" / [])."""
    return value not in (None, "", [])


def _is_classified(fields: dict[str, Any]) -> bool:
    return all(_present(fields.get(f)) for f in _CLASSIFICATION_FIELDS)


class ForwardSearchService:
    def __init__(
        self, repo: AirtableRepo, gemini: GeminiClient, settings: Settings
    ) -> None:
        self.repo = repo
        self.gemini = gemini
        self.settings = settings

    # ------------------------------------------------------------------
    async def run_for_company(self, company_id: str, *, api_job_id: Any) -> dict[str, Any]:
        """Run the full forward search for one company. Returns a JSON-safe
        summary (used as the job ``result``). Raises on unrecoverable errors so
        the caller marks the job failed."""
        if not self.settings.gemini_api_key:
            raise GeminiError("GEMINI_API_KEY is not configured")

        company = await asyncio.to_thread(self.repo.get_company_for_search, company_id)
        if company is None:
            raise ValueError(f"Company {company_id} not found")

        run_id = f"fwd_{secrets.token_hex(4)}"
        token = current_request_id.set(run_id)
        started = datetime.now(UTC)
        try:
            fields = company.get("fields", {})
            logger.info(
                "forward_search.start company=%s name=%s job=%s",
                company_id,
                _text(fields.get("Company name")),
                api_job_id,
            )
            await asyncio.to_thread(
                self.repo.update_company_fields,
                company_id,
                {"Grant Search Status": "Search in Progress"},
            )

            # Existing matches → grant dedup (captured before any write-back).
            existing_match_ids = [
                m for m in (fields.get("Search Matches") or []) if isinstance(m, str)
            ]

            if not _is_classified(fields):
                fields = await self._classify(company_id, fields)

            grants = await asyncio.to_thread(self.repo.list_grants_for_filtering)
            funnel = run_company_funnel(grants, fields)

            already = (
                await asyncio.to_thread(
                    self.repo.grant_ids_with_existing_matches, existing_match_ids
                )
                if existing_match_ids
                else set()
            )
            eligible = [g for g in funnel.eligible if g["id"] not in already]
            deduped = funnel.eligible_count - len(eligible)

            cap = self.settings.forward_search_max_grants
            capped = 0
            if cap and len(eligible) > cap:
                capped = len(eligible) - cap
                logger.warning(
                    "forward_search.capped company=%s eligible=%d cap=%d skipped=%d",
                    company_id,
                    len(eligible),
                    cap,
                    capped,
                )
                eligible = eligible[:cap]

            outcome = await self._sanity_check_grants(
                company_id, fields, eligible, api_job_id
            )

            await asyncio.to_thread(
                self.repo.update_company_fields,
                company_id,
                {"Grant Search Status": "Search Completed"},
            )
            logger.info(
                "forward_search.done company=%s created=%d failed=%d errors=%d",
                company_id,
                outcome["created"],
                outcome["failed"],
                outcome["errors"],
            )
            return {
                "company_record_id": company_id,
                "reviewed": funnel.reviewed,
                "eligible": funnel.eligible_count,
                "deduped": deduped,
                "capped": capped,
                "evaluated": outcome["evaluated"],
                "matches_created": outcome["created"],
                "dismissed": outcome["failed"],
                "errors": outcome["errors"],
                "tokens": outcome["tokens"],
                "duration_s": round((datetime.now(UTC) - started).total_seconds(), 1),
                "completed_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
        finally:
            current_request_id.reset(token)

    # ------------------------------------------------------------------
    async def _classify(self, company_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Classify an unclassified company and write the result back. Returns the
        refreshed company fields (with recomputed formulas, e.g. Years)."""
        description = _text(fields.get("Company description"))
        classification, _usage = await self.gemini.classify_company(
            company_description=description, today=date.today().isoformat()
        )
        model = CompanyClassification.model_validate(classification)
        write = model.to_company_fields()
        # Append the classification profile to the description (mirrors n8n).
        write["Company description"] = f"{description}\n\n{model.to_markdown()}"
        updated = await asyncio.to_thread(
            self.repo.update_company_fields, company_id, write
        )
        logger.info("forward_search.classified company=%s", company_id)
        return updated.get("fields", {})

    # ------------------------------------------------------------------
    async def _sanity_check_grants(
        self,
        company_id: str,
        company_fields: dict[str, Any],
        eligible: list[dict[str, Any]],
        api_job_id: Any,
    ) -> dict[str, Any]:
        """LLM gate over eligible grants; create a Search Match per PASS."""
        company_description = _build_company_description(company_fields)
        today = date.today().isoformat()
        semaphore = asyncio.Semaphore(max(1, self.settings.forward_search_concurrency))

        async def evaluate(grant: dict[str, Any]) -> dict[str, Any]:
            gfields = grant.get("fields", {})
            async with semaphore:
                try:
                    decision, usage = await self.gemini.sanity_check(
                        today=today,
                        company_description=company_description,
                        grant_name=_text(gfields.get("Name")),
                        grant_description=_build_grant_description(gfields),
                    )
                except GeminiError as exc:
                    return {"status": "error", "grant": grant, "error": str(exc)}
                gate = evaluate_gate(decision)
                return {
                    "status": gate.verdict,
                    "grant": grant,
                    "decision": decision,
                    "usage": usage,
                    "reasons": gate.reasons,
                }

        results = await asyncio.gather(*(evaluate(g) for g in eligible))

        tokens = {"prompt": 0, "candidates": 0, "total": 0}
        fail_reasons: Counter[str] = Counter()
        created = failed = errors = 0

        for res in results:
            usage = res.get("usage")
            if usage:
                for key in tokens:
                    tokens[key] += usage.get(key, 0)
            if res["status"] == "pass":
                match_fields = build_search_match_fields(
                    res["decision"],
                    grant_id=res["grant"]["id"],
                    company_id=company_id,
                    api_job_id=str(api_job_id),
                    notification_status=_MATCH_NOTIFICATION_STATUS,
                )
                await asyncio.to_thread(self.repo.create_search_match, match_fields)
                await asyncio.sleep(_WRITE_PACING_SECONDS)
                created += 1
            elif res["status"] == "fail":
                failed += 1
                for reason in res.get("reasons") or ["unspecified"]:
                    fail_reasons[reason] += 1
            else:
                errors += 1

        return {
            "evaluated": len(results),
            "created": created,
            "failed": failed,
            "errors": errors,
            "tokens": tokens,
            "fail_reasons": fail_reasons,
        }
