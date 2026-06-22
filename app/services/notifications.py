"""Daily client-notification digest.

At 15:00 Europe/Vilnius, group all ``Notification Status = Pending`` reverse-search
matches by company and email each notification-enabled company a single digest of
its new matches. Matches then move to Sent / Failed / Skipped.

Gate: a company is notification-enabled iff ``Notification Customer`` is one of
Basic / Pro / Custom (i.e. not ``False`` and not blank), re-checked here at send
time. Companies with no email are skipped and logged.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
from typing import Any

from app.config import Settings
from app.repositories.airtable import AirtableRepo
from app.services.smtp_client import SmtpClient, SmtpError

logger = logging.getLogger(__name__)

# Notification Customer values that count as "enabled".
_ENABLED_TIERS = {"Basic", "Pro", "Custom"}

_STATUS_PENDING = "Pending"
_STATUS_SENT = "Sent"
_STATUS_FAILED = "Failed"
_STATUS_SKIPPED = "Skipped"


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _parse_raw_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _match_title(match_fields: dict[str, Any], raw: dict[str, Any]) -> str:
    return (
        _text(match_fields.get("Name"))
        or _text(raw.get("Short Grant Title"))
        or "New grant match"
    )


def render_digest(
    company_name: str,
    matches: list[dict[str, Any]],
    settings: Settings,
) -> tuple[str, str, str]:
    """Return ``(subject, text_body, html_body)`` for a company's digest."""
    count = len(matches)
    who = company_name or "your organisation"
    plural = "es" if count != 1 else ""
    subject = f"{count} new grant match{plural} for {who}"

    text_parts = [
        f"Hello {who},",
        "",
        f"We found {count} new grant match{plural} for you:",
        "",
    ]
    html_blocks = [
        f"<p>Hello {html.escape(who)},</p>",
        f"<p>We found <strong>{count}</strong> new grant match{plural} for you:</p>",
    ]

    for i, match in enumerate(matches, start=1):
        fields = match.get("fields", {})
        raw = _parse_raw_json(fields.get("Raw Json"))
        title = _match_title(fields, raw)
        description = _text(fields.get("Match Description")) or "\n".join(
            p for p in [_text(raw.get("Hook sentence")), _text(raw.get("Match Summary"))] if p
        )
        fits = _text(raw.get("formatted_fits"))

        text_parts.append(f"{i}. {title}")
        if description:
            text_parts.append(description)
        if fits:
            text_parts.append(fits)
        text_parts.append("")

        block = [f"<h3 style=\"margin:24px 0 4px\">{html.escape(title)}</h3>"]
        if description:
            safe = html.escape(description).replace("\n", "<br>")
            block.append(f'<p style="margin:0 0 8px">{safe}</p>')
        if fits:
            safe_fits = html.escape(fits).replace("\n", "<br>")
            block.append(
                f'<p style="margin:0;color:#555;font-size:14px">{safe_fits}</p>'
            )
        if settings.client_portal_url:
            block.append(
                f'<p style="margin:8px 0"><a href="{html.escape(settings.client_portal_url)}">'
                "View in your dashboard</a></p>"
            )
        html_blocks.append("".join(block))

    text_parts.append("— Cogrant")
    html_blocks.append('<p style="margin-top:24px">— Cogrant</p>')

    html_body = (
        '<div style="font-family:Arial,Helvetica,sans-serif;max-width:640px;'
        'color:#222;line-height:1.5">' + "".join(html_blocks) + "</div>"
    )
    return subject, "\n".join(text_parts), html_body


def _is_enabled(company_fields: dict[str, Any]) -> bool:
    return _text(company_fields.get("Notification Customer")) in _ENABLED_TIERS


class NotificationService:
    def __init__(
        self, repo: AirtableRepo, smtp: SmtpClient, settings: Settings
    ) -> None:
        self.repo = repo
        self.smtp = smtp
        self.settings = settings

    async def run_daily(self) -> None:
        if not self.settings.email_enabled:
            logger.info("notifications.skip reason=email_disabled")
            return

        matches = await asyncio.to_thread(self.repo.list_pending_notification_matches)
        if not matches:
            logger.info("notifications.none_pending")
            return

        # Group pending matches by their (single) linked company.
        groups: dict[str, list[dict[str, Any]]] = {}
        for match in matches:
            company_link = match.get("fields", {}).get("Company") or []
            if not company_link:
                continue
            groups.setdefault(company_link[0], []).append(match)

        companies = await asyncio.to_thread(
            self.repo.get_companies_by_ids, list(groups)
        )

        sent = skipped = failed = 0
        for company_id, company_matches in groups.items():
            cfields = companies.get(company_id, {})
            email = _text(cfields.get("Email")).strip()
            company_name = _text(cfields.get("Company name"))

            if not _is_enabled(cfields) or not email:
                reason = "not_enabled" if not _is_enabled(cfields) else "no_email"
                logger.info(
                    "notifications.skip company=%s reason=%s matches=%d",
                    company_id,
                    reason,
                    len(company_matches),
                )
                await self._mark(company_matches, _STATUS_SKIPPED)
                skipped += len(company_matches)
                continue

            subject, text_body, html_body = render_digest(
                company_name, company_matches, self.settings
            )
            recipient = self.settings.email_from if self.settings.email_dry_run else email
            try:
                await self.smtp.send(
                    to=recipient,
                    subject=subject,
                    text_body=text_body,
                    html_body=html_body,
                    bcc=self.settings.email_bcc,
                )
            except SmtpError:
                logger.exception("notifications.send_failed company=%s", company_id)
                await self._mark(company_matches, _STATUS_FAILED)
                failed += len(company_matches)
                continue
            await self._mark(company_matches, _STATUS_SENT)
            sent += len(company_matches)

        logger.info(
            "notifications.done companies=%d sent=%d skipped=%d failed=%d",
            len(groups),
            sent,
            skipped,
            failed,
        )

    async def _mark(self, matches: list[dict[str, Any]], status: str) -> None:
        for match in matches:
            await asyncio.to_thread(
                self.repo.set_match_notification_status, match["id"], status
            )
            await asyncio.sleep(0.2)  # stay under Airtable's 5 req/s
