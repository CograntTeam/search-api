"""Reverse-search orchestrator.

Replaces the n8n workflow ``2 - Reverse Search Initiation`` + its fan-out to
``1.1 A``. For each Idle, enriched, not-closing-soon grant it:

1. claims the grant (Idle -> In Progress),
2. filters all companies down to the eligible set (recording the funnel),
3. runs the Gemini sanity check per eligible company (bounded concurrency),
4. creates a Search Match only for PASS verdicts (dedup against existing ones),
5. writes the funnel/summary to the grant's Reverse Search Log and marks it
   Completed.

The n8n coordination table + polling is replaced by awaiting bounded in-process
tasks. Airtable writes are paced to stay under the 5 req/s per-base limit.
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
from app.repositories.airtable import AirtableRepo
from app.services.gemini_client import GeminiClient, GeminiError
from app.services.match_filters import FilterFunnel, grant_precondition, run_filter_funnel
from app.services.match_mapping import build_search_match_fields, evaluate_gate

logger = logging.getLogger(__name__)

# Spacing between Airtable writes so a grant with many matches stays well under
# Airtable's 5 req/s per-base ceiling.
_WRITE_PACING_SECONDS = 0.25


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _build_company_description(fields: dict[str, Any]) -> str:
    """Mirror workflow 2's ``Prepare Fields22`` company description assembly."""
    country = _text(fields.get("Country"))
    description = _text(fields.get("Company description"))
    preference = _text(fields.get("Company Preference Profile"))
    return f"Company registration country: {country}\n\n{description}\n{preference}"


def _build_grant_description(fields: dict[str, Any]) -> str:
    """``Grant Description`` + ``Grant Details``, as the workflow concatenates."""
    return f"{_text(fields.get('Grant Description'))}\n{_text(fields.get('Grant Details'))}"


class ReverseSearchService:
    def __init__(
        self,
        repo: AirtableRepo,
        gemini: GeminiClient,
        settings: Settings,
    ) -> None:
        self.repo = repo
        self.gemini = gemini
        self.settings = settings

    # ------------------------------------------------------------------
    async def run_once(self) -> None:
        """Process every grant currently awaiting a reverse search."""
        if not self.settings.gemini_api_key:
            logger.warning("reverse_search.skip reason=gemini_api_key_missing")
            return

        grants = await asyncio.to_thread(self.repo.list_idle_grants)
        if not grants:
            return
        logger.info("reverse_search.poll idle_grants=%d", len(grants))

        companies: list[dict[str, Any]] | None = None
        for grant in grants:
            reason = grant_precondition(grant.get("fields", {}))
            if reason == "not enriched":
                # Leave Idle; it'll be picked up once enrichment completes.
                continue
            if reason:
                # Permanent skip (deadline). Mark Completed so we don't re-poll it.
                await asyncio.to_thread(
                    self.repo.update_grant_reverse_search,
                    grant["id"],
                    status="Completed",
                    log=self._skip_log(reason),
                )
                continue

            if companies is None:
                companies = await asyncio.to_thread(self.repo.list_companies_for_filtering)
            await self._process_grant(grant, companies)

    # ------------------------------------------------------------------
    async def _process_grant(
        self, grant: dict[str, Any], companies: list[dict[str, Any]]
    ) -> None:
        grant_id = grant["id"]
        fields = grant.get("fields", {})
        grant_name = _text(fields.get("Name"))
        run_id = f"rev_{secrets.token_hex(4)}"
        token = current_request_id.set(run_id)
        started = datetime.now(UTC)
        try:
            await asyncio.to_thread(
                self.repo.update_grant_reverse_search, grant_id, status="In Progress"
            )
            logger.info("reverse_search.start grant=%s name=%s", grant_id, grant_name)

            funnel = run_filter_funnel(companies, fields)

            # Dedup: drop companies that already have a match for this grant.
            existing_match_ids = [
                m for m in (fields.get("Search Matches") or []) if isinstance(m, str)
            ]
            already = (
                await asyncio.to_thread(
                    self.repo.company_ids_with_existing_matches, existing_match_ids
                )
                if existing_match_ids
                else set()
            )
            eligible = [c for c in funnel.eligible if c["id"] not in already]
            deduped = funnel.eligible_count - len(eligible)

            cap = self.settings.reverse_search_max_companies
            capped = 0
            if cap and len(eligible) > cap:
                capped = len(eligible) - cap
                eligible = eligible[:cap]

            outcome = await self._sanity_check_companies(grant, fields, eligible)

            log = self._success_log(
                grant_name=grant_name,
                run_id=run_id,
                started=started,
                funnel=funnel,
                deduped=deduped,
                capped=capped,
                outcome=outcome,
            )
            await asyncio.to_thread(
                self.repo.update_grant_reverse_search,
                grant_id,
                status="Completed",
                log=log,
            )
            logger.info(
                "reverse_search.done grant=%s created=%d failed=%d errors=%d",
                grant_id,
                outcome["created"],
                outcome["failed"],
                outcome["errors"],
            )
        except Exception as exc:  # noqa: BLE001 — one grant must not kill the loop
            logger.exception("reverse_search.error grant=%s", grant_id)
            await asyncio.to_thread(
                self.repo.update_grant_reverse_search,
                grant_id,
                status="Completed",
                log=self._error_log(run_id, started, exc),
            )
        finally:
            current_request_id.reset(token)

    # ------------------------------------------------------------------
    async def _sanity_check_companies(
        self,
        grant: dict[str, Any],
        grant_fields: dict[str, Any],
        eligible: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Run the LLM gate over eligible companies and create PASS matches."""
        grant_id = grant["id"]
        grant_name = _text(grant_fields.get("Name"))
        grant_description = _build_grant_description(grant_fields)
        today = date.today().isoformat()
        semaphore = asyncio.Semaphore(max(1, self.settings.reverse_search_concurrency))

        async def evaluate(company: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                try:
                    decision, usage = await self.gemini.sanity_check(
                        today=today,
                        company_description=_build_company_description(
                            company.get("fields", {})
                        ),
                        grant_name=grant_name,
                        grant_description=grant_description,
                    )
                except GeminiError as exc:
                    return {"status": "error", "company": company, "error": str(exc)}
                gate = evaluate_gate(decision)
                return {
                    "status": gate.verdict,
                    "company": company,
                    "decision": decision,
                    "usage": usage,
                    "reasons": gate.reasons,
                }

        results = await asyncio.gather(*(evaluate(c) for c in eligible))

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
                    grant_id=grant_id,
                    company_id=res["company"]["id"],
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

    # ------------------------------------------------------------------
    # log rendering
    # ------------------------------------------------------------------
    @staticmethod
    def _skip_log(reason: str) -> str:
        ts = datetime.now(UTC).isoformat(timespec="seconds")
        return f"Reverse Search — {ts}\nRESULT: SKIPPED ({reason})"

    @staticmethod
    def _error_log(run_id: str, started: datetime, exc: Exception) -> str:
        ts = datetime.now(UTC).isoformat(timespec="seconds")
        return (
            f"RESULT: ERROR\nReverse Search — {ts}\nRun ID: {run_id}\n"
            f"Started: {started.isoformat(timespec='seconds')}\n\n"
            f"{type(exc).__name__}: {exc}"
        )

    @staticmethod
    def _success_log(
        *,
        grant_name: str,
        run_id: str,
        started: datetime,
        funnel: FilterFunnel,
        deduped: int,
        capped: int,
        outcome: dict[str, Any],
    ) -> str:
        finished = datetime.now(UTC)
        lines = [
            f"Reverse Search — {finished.isoformat(timespec='seconds')}",
            f"Run ID: {run_id}",
            f"Grant: {grant_name}",
            f"Duration: {(finished - started).total_seconds():.1f}s",
            "",
            f"Companies reviewed: {funnel.reviewed}",
            "── Filter funnel ──",
        ]
        for stage in funnel.stages:
            lines.append(
                f"  {stage.name:<24} -{stage.dropped:<5} -> {stage.remaining} remaining"
            )
        lines.append(f"Eligible after filters: {funnel.eligible_count}")
        if deduped:
            lines.append(f"Skipped (already matched): {deduped}")
        if capped:
            lines.append(f"Skipped (per-grant cap): {capped}")

        tokens = outcome["tokens"]
        lines += [
            "",
            "── Sanity check ──",
            f"Evaluated: {outcome['evaluated']}  →  "
            f"{outcome['created']} matches created, "
            f"{outcome['failed']} dismissed, {outcome['errors']} errors",
            f"Gemini tokens: {tokens['prompt']} in / "
            f"{tokens['candidates']} out / {tokens['total']} total",
        ]

        fail_reasons: Counter[str] = outcome["fail_reasons"]
        if fail_reasons:
            lines.append("")
            lines.append("── Dismissal reasons ──")
            for reason, count in fail_reasons.most_common(8):
                lines.append(f"  {count:>4}  {reason}")
        return "\n".join(lines)
