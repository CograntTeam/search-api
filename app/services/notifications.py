"""Daily client-notification digest.

At 15:00 Europe/Vilnius, group all ``Notification Status = Pending`` reverse-search
matches by company and email each notification-enabled company a single digest of
its new matches. Matches then move to Sent / Failed / Skipped.

Gate: a company is notification-enabled iff ``Notification Customer`` is ``Pro``,
re-checked here at send time. Companies with no email are skipped and logged.

:func:`render_digest` produces the designed "New Opportunities" email. Per-card
funding / deadline / agency / programme come from the match's ``Grant Details JSON``
lookup (parsed via :class:`GrantDetails`); every such field degrades gracefully —
a grant the upstream agents have not enriched simply shows fewer details, never a
placeholder or fabricated value.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import Settings
from app.models.grant_details import GrantDetails
from app.repositories.airtable import AirtableRepo
from app.services.smtp_client import SmtpClient, SmtpError

logger = logging.getLogger(__name__)

# Notification Customer values that count as "enabled". Restricted to Pro only.
_ENABLED_TIERS = {"Pro"}

_STATUS_PENDING = "Pending"
_STATUS_SENT = "Sent"
_STATUS_FAILED = "Failed"
_STATUS_SKIPPED = "Skipped"

# --- Design tokens (from "Email - New Opportunities") ---------------------
_PLANNER_FALLBACK = "https://planner.cogrant.eu"
_PURPLE = "#524CE7"
_INK = "#0F0F0F"
_MUTED = "#737373"
_FONT = "'Satoshi','Helvetica Neue',Arial,sans-serif"
_DEADLINE_SOON_DAYS = 110  # days-left rendered in accent purple at or under this

_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)
_WORDS = (
    "zero", "one", "two", "three", "four", "five",
    "six", "seven", "eight", "nine", "ten",
)

_STYLE = """
  /* client resets */
  body { margin: 0; padding: 0; width: 100% !important; background: #F2F2F2; -webkit-font-smoothing: antialiased; -webkit-text-size-adjust: 100%; }
  table { border-collapse: collapse; mso-table-lspace: 0; mso-table-rspace: 0; }
  img { border: 0; line-height: 100%; outline: none; text-decoration: none; -ms-interpolation-mode: bicubic; display: block; }
  a { text-decoration: none; }
  .opp-row:hover .opp-card { border-color: #524CE7 !important; }
  .opp-row:hover .opp-cta { color: #4139D4 !important; }
  @media only screen and (max-width: 620px) {
    .container { width: 100% !important; }
    .px { padding-left: 22px !important; padding-right: 22px !important; }
    .hero-h { font-size: 30px !important; line-height: 1.14 !important; }
    .stack { display: block !important; width: 100% !important; }
    .stack-r { text-align: left !important; padding-top: 6px !important; }
  }
"""

# Brand wordmark. Inline SVG renders in Apple Mail and similar; Gmail/Outlook
# strip it (the aria-label is the accessible fallback). Swap to a hosted <img>
# if a universally-rendered logo is needed.
_LOGO_SVG = (
    '<svg width="118" height="28" viewBox="0 0 319.45 76.81" '
    'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Cogrant">'
    '<g fill="#0F0F0F">'
    '<path fill="#524CE7" d="M68.99,41.42l-5.58-4.06,5.58-4.06c5.25-3.82,2.55-12.12-3.94-12.12h-6.9l2.13-6.56c2.01-6.17-5.06-11.31-10.31-7.49l-5.58,4.06-2.13-6.56c-2.01-6.17-10.74-6.17-12.75,0l-2.13,6.56-5.58-4.06c-5.25-3.82-12.32,1.32-10.31,7.49l2.13,6.56h-6.9c-6.49,0-9.19,8.31-3.94,12.12l5.58,4.06-5.58,4.06c-5.25,3.82-2.55,12.12,3.94,12.12h6.9l-2.13,6.56c-2.01,6.17,5.06,11.31,10.31,7.49l5.58-4.06,2.13,6.56c2.01,6.17,10.74,6.17,12.75,0l2.13-6.56,5.58,4.06c5.25,3.82,12.32-1.32,10.31-7.49l-2.13-6.56h6.9c6.49,0,9.19-8.31,3.94-12.12Z"></path>'
    '<path d="M121.25,51.38c-2.33,1.56-5.12,2.35-8.38,2.35-2.87,0-5.38-.68-7.53-2.05-2.15-1.37-3.81-3.3-4.99-5.8-1.17-2.5-1.76-5.4-1.76-8.7s.59-6.39,1.76-8.87c1.17-2.48,2.84-4.39,4.99-5.74,2.15-1.35,4.66-2.02,7.53-2.02,3.22,0,5.94.77,8.18,2.31,2.24,1.54,3.77,3.71,4.6,6.49h8.8c-.52-3.43-1.76-6.4-3.72-8.9-1.96-2.5-4.43-4.44-7.43-5.83-3-1.39-6.39-2.09-10.17-2.09-4.61,0-8.67,1.02-12.19,3.06-3.52,2.04-6.25,4.91-8.18,8.6-1.93,3.69-2.9,8.02-2.9,12.97s.95,9.33,2.84,13.01c1.89,3.67,4.54,6.52,7.95,8.54,3.41,2.02,7.44,3.03,12.09,3.03,3.78,0,7.2-.7,10.27-2.09,3.06-1.39,5.61-3.35,7.63-5.87,2.02-2.52,3.33-5.43,3.94-8.73h-8.67c-.78,2.65-2.34,4.76-4.66,6.32Z"></path>'
    '<path d="M162.97,30c-2.59-1.43-5.55-2.15-8.9-2.15s-6.26.72-8.87,2.15-4.65,3.42-6.13,5.96c-1.48,2.54-2.22,5.49-2.22,8.83s.74,6.29,2.22,8.83c1.48,2.54,3.52,4.53,6.13,5.96,2.61,1.43,5.56,2.15,8.87,2.15s6.31-.72,8.9-2.15c2.59-1.43,4.62-3.42,6.1-5.96,1.48-2.54,2.22-5.49,2.22-8.83s-.74-6.29-2.22-8.83c-1.48-2.54-3.51-4.53-6.1-5.96ZM162.15,49.95c-.78,1.48-1.87,2.63-3.26,3.46-1.39.83-3,1.24-4.82,1.24s-3.42-.41-4.79-1.24c-1.37-.82-2.46-1.98-3.26-3.46-.8-1.48-1.21-3.19-1.21-5.15s.4-3.73,1.21-5.18c.8-1.46,1.89-2.6,3.26-3.42,1.37-.83,2.97-1.24,4.79-1.24s3.43.41,4.82,1.24c1.39.83,2.48,1.97,3.26,3.42.78,1.46,1.17,3.18,1.17,5.18s-.39,3.67-1.17,5.15Z"></path>'
    '<path d="M199.32,33.18c-.81-1.48-1.96-2.68-3.47-3.61-1.96-1.19-4.3-1.79-7.04-1.79-3.04,0-5.72.72-8.02,2.15-2.3,1.43-4.1,3.38-5.38,5.83-1.28,2.46-1.92,5.25-1.92,8.38s.63,5.91,1.89,8.34c1.26,2.43,3.02,4.35,5.28,5.74,2.26,1.39,4.91,2.09,7.95,2.09,2.74,0,5.15-.61,7.24-1.83,1.45-.84,2.57-1.89,3.39-3.13v5.41c0,2.69-.81,4.83-2.44,6.42-1.63,1.59-3.81,2.38-6.55,2.38-2.48,0-4.47-.59-5.96-1.76s-2.38-2.8-2.64-4.89h-7.95c.52,4.3,2.26,7.69,5.22,10.17,2.95,2.48,6.74,3.72,11.34,3.72,3.52,0,6.53-.71,9.03-2.12,2.5-1.41,4.43-3.43,5.8-6.06,1.37-2.63,2.05-5.73,2.05-9.29v-30.57h-7.37l-.44,4.42ZM197.97,48.87c-.72,1.41-1.74,2.52-3.06,3.32-1.33.8-2.9,1.21-4.73,1.21s-3.26-.41-4.56-1.24c-1.3-.83-2.31-1.94-3.03-3.36-.72-1.41-1.08-3.03-1.08-4.86s.35-3.37,1.04-4.76c.69-1.39,1.69-2.49,3-3.29,1.3-.8,2.85-1.21,4.63-1.21s3.47.39,4.79,1.17c1.33.78,2.34,1.86,3.03,3.23.69,1.37,1.04,2.99,1.04,4.86s-.36,3.51-1.08,4.92Z"></path>'
    '<path d="M228.25,28.24c-2.56,0-4.65.67-6.26,2.02-1.11.93-1.91,2.05-2.46,3.35l-.47-4.78h-7.5v32.14h7.95v-15.65c0-3.13.82-5.46,2.48-7.01,1.65-1.54,3.93-2.31,6.84-2.31h2.93v-7.37c-.65-.17-1.26-.28-1.83-.33-.57-.04-1.13-.06-1.7-.06Z"></path>'
    '<path d="M255.96,29.32c-2.09-1.02-4.58-1.53-7.5-1.53s-5.4.48-7.59,1.43c-2.2.96-3.89,2.29-5.08,4.01-1.2,1.72-1.79,3.75-1.79,6.1h6.78c0-1.69.63-3.01,1.89-3.94,1.26-.93,3.06-1.4,5.41-1.4,1.35,0,2.52.21,3.52.62,1,.41,1.77,1.07,2.31,1.96.54.89.82,2.08.82,3.55v.72l-9.65.78c-3.82.3-6.77,1.34-8.83,3.1-2.06,1.76-3.1,4.12-3.1,7.07s.99,5.42,2.97,7.27c1.98,1.85,4.66,2.77,8.05,2.77,2.52,0,4.77-.52,6.75-1.56,1.98-1.04,3.31-2.39,4.01-4.04l.59,4.76h6.97v-19.56c0-3-.56-5.51-1.69-7.53-1.13-2.02-2.74-3.54-4.82-4.56ZM254.78,47.93c0,2.48-.73,4.41-2.18,5.8-1.46,1.39-3.42,2.09-5.9,2.09-1.74,0-3.09-.37-4.04-1.11-.96-.74-1.43-1.83-1.43-3.26,0-1.3.52-2.36,1.56-3.16,1.04-.8,2.8-1.31,5.28-1.53l6.71-.52v1.69Z"></path>'
    '<path d="M284.97,27.78c-2.17,0-4.18.46-6.03,1.37-1.85.91-3.27,2.17-4.27,3.78l-.65-4.17h-7.37v32.2h7.95v-17.34c0-2.61.69-4.69,2.09-6.26,1.39-1.56,3.33-2.35,5.8-2.35,2.09,0,3.74.66,4.95,1.99,1.22,1.33,1.83,3.34,1.83,6.03v17.93h7.95v-19.82c0-4.13-1.03-7.39-3.1-9.78-2.07-2.39-5.12-3.59-9.16-3.59Z"></path>'
    '<polygon points="319.45 28.76 313.26 28.76 313.26 18.72 305.3 18.72 305.3 28.76 299.11 28.76 299.11 35.41 305.3 35.41 305.3 60.96 313.26 60.96 313.26 35.41 319.45 35.41 319.45 28.76"></polygon>'
    "</g></svg>"
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _lookup_first(value: Any) -> Any:
    """First scalar from an Airtable lookup field (lookups arrive as a list)."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


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


def _parse_grant_details(value: Any) -> GrantDetails | None:
    """Parse a Search Match ``Grant Details JSON`` lookup into a GrantDetails.

    The field is an Airtable lookup, so it arrives as ``[json_string]`` (it may
    also be a bare string or already-parsed dict). Returns ``None`` when absent
    or unparseable; pydantic ``ValidationError`` is a ``ValueError`` subclass so
    a malformed payload degrades to ``None`` rather than raising.
    """
    if isinstance(value, list):
        value = value[0] if value else None
    if not value:
        return None
    try:
        data = json.loads(value) if isinstance(value, str) else value
        if not isinstance(data, dict):
            return None
        return GrantDetails.model_validate(data)
    except (ValueError, TypeError):
        return None


def _match_title(match_fields: dict[str, Any], raw: dict[str, Any]) -> str:
    return (
        _text(match_fields.get("Name"))
        or _text(raw.get("Short Grant Title"))
        or "New grant match"
    )


def _count_word(n: int) -> str:
    return _WORDS[n] if 0 <= n <= 10 else str(n)


def _money_symbol(currency: str | None) -> str:
    return {"EUR": "€", "USD": "$", "GBP": "£"}.get((currency or "EUR").upper(), "")


def _compact_amount(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    if n >= 1_000:
        return f"{round(n / 1_000)}K"
    return str(n)


def _max_ticket(gd: GrantDetails | None) -> int:
    """Largest per-project maximum funding ticket across tiers, or 0."""
    if gd is None or gd.financials is None:
        return 0
    maxs = [
        t.maximum_funding_ticket
        for t in (gd.financials.funding_tiers or [])
        if isinstance(t.maximum_funding_ticket, int)
    ]
    return max(maxs) if maxs else 0


def _funding_label(gd: GrantDetails | None) -> str:
    """A human funding range like ``€100K–€300K`` / ``Up to €500K``; "" if none."""
    if gd is None or gd.financials is None:
        return ""
    sym = _money_symbol(gd.financials.currency)
    tiers = gd.financials.funding_tiers or []
    mins = [t.minimum_funding_ticket for t in tiers if isinstance(t.minimum_funding_ticket, int)]
    maxs = [t.maximum_funding_ticket for t in tiers if isinstance(t.maximum_funding_ticket, int)]
    lo = min(mins) if mins else 0
    hi = max(maxs) if maxs else 0
    if lo and hi and lo != hi:
        return f"{sym}{_compact_amount(lo)}–{sym}{_compact_amount(hi)}"
    if hi:
        return f"Up to {sym}{_compact_amount(hi)}"
    if lo:
        return f"From {sym}{_compact_amount(lo)}"
    return ""


def _parse_iso_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _fmt_date(d: date) -> str:
    return f"{d.day} {_MONTHS[d.month - 1]} {d.year}"


def _deadline_and_days(gd: GrantDetails | None, today: date) -> tuple[str, int | None]:
    """Nearest upcoming stage deadline as ``("15 Sep 2026", days_left)``.

    Returns ``("", None)`` for continuous calls or grants without a future stage.
    """
    if gd is None or gd.timelines is None:
        return "", None
    future = [
        d
        for st in (gd.timelines.application_stages or [])
        if (d := _parse_iso_date(st.deadline_date)) is not None and d >= today
    ]
    if not future:
        return "", None
    nearest = min(future)
    return _fmt_date(nearest), (nearest - today).days


def _deadline_from_field(value: Any, today: date) -> tuple[str, int | None]:
    """Nearest upcoming deadline from the grant's ``Grant Deadline`` lookup.

    The lookup arrives as one or more ISO date strings; we pick the nearest one
    still in the future (mirroring :func:`_deadline_and_days`). ``("", None)`` if
    none are upcoming or the field is empty.
    """
    values = value if isinstance(value, list) else [value]
    future = [
        d for v in values if (d := _parse_iso_date(v)) is not None and d >= today
    ]
    if not future:
        return "", None
    nearest = min(future)
    return _fmt_date(nearest), (nearest - today).days


def _scope_label(gd: GrantDetails | None) -> str:
    if gd is None or gd.eligibility_and_consortia is None:
        return ""
    level = _text(gd.eligibility_and_consortia.geography_level).strip()
    return {"European": "EU-wide"}.get(level, level)


def _subtitle(gd: GrantDetails | None) -> str:
    """``Managing authority · Programme`` (either part alone is fine)."""
    if gd is None or gd.core_metadata is None:
        return ""
    cm = gd.core_metadata
    parts = [
        p
        for p in (_text(cm.managing_authority).strip(), _text(cm.program_name).strip())
        if p
    ]
    return " · ".join(parts)


def _card_title(fields: dict[str, Any], raw: dict[str, Any], gd: GrantDetails | None) -> str:
    if gd is not None and gd.core_metadata is not None:
        short = _text(gd.core_metadata.short_grant_name).strip()
        if short:
            return short
    return _match_title(fields, raw)


def _card_description(fields: dict[str, Any], raw: dict[str, Any]) -> str:
    return (
        _text(raw.get("Hook sentence")).strip()
        or _text(raw.get("Match Summary")).strip()
        or _text(fields.get("Match Description")).strip()
    )


def _eyebrow(match_type: str, eligibility: str, scope: str) -> str:
    primary = match_type or eligibility
    return " · ".join(p for p in (primary, scope) if p)


def _render_card(
    *,
    href: str,
    eyebrow: str,
    title: str,
    subtitle: str,
    desc: str,
    funding: str,
    date_str: str,
    days: int | None,
    last: bool,
) -> str:
    margin_bottom = "6px" if last else "14px"
    eyebrow_html = (
        f'<div style="font-size:11px;font-weight:500;letter-spacing:1.5px;'
        f'text-transform:uppercase;color:{_PURPLE};">{html.escape(eyebrow)}</div>'
        if eyebrow
        else ""
    )
    subtitle_html = (
        f'<div style="font-size:13px;color:{_MUTED};margin-top:3px;">{html.escape(subtitle)}</div>'
        if subtitle
        else ""
    )
    desc_html = (
        f'<p style="margin:14px 0 0 0;font-size:14px;line-height:1.55;color:#525252;">'
        f"{html.escape(desc)}</p>"
        if desc
        else ""
    )

    meta: list[str] = []
    if funding:
        meta.append(
            f'<span style="display:inline-block;font-size:13px;color:{_MUTED};">Funding</span>'
            f'<span style="display:inline-block;font-size:14px;font-weight:600;color:{_INK};'
            f'margin-left:6px;">{html.escape(funding)}</span>'
        )
    if date_str:
        if meta:
            meta.append(
                '<span style="display:inline-block;color:#D0D1D4;margin:0 9px;">·</span>'
            )
        meta.append(
            f'<span style="display:inline-block;font-size:14px;font-weight:600;'
            f'color:{_INK};">{html.escape(date_str)}</span>'
        )
        if days is not None:
            color = _PURPLE if days <= _DEADLINE_SOON_DAYS else _MUTED
            label = "1 day left" if days == 1 else f"{days} days left"
            meta.append(
                f'<span style="display:inline-block;font-size:12px;font-weight:500;'
                f'color:{color};margin-left:6px;">{label}</span>'
            )
    meta_html = "".join(meta)

    return (
        f'<a class="opp-row" href="{html.escape(href)}" '
        'style="display:block;text-decoration:none;color:inherit;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'class="opp-card" style="border:1px solid #E5E5E5;border-radius:14px;'
        f'margin-bottom:{margin_bottom};transition:border-color 150ms;">'
        '<tr><td style="padding:20px 22px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        '<td class="stack" style="vertical-align:top;">'
        f"{eyebrow_html}"
        f'<div style="font-size:19px;font-weight:600;letter-spacing:-0.3px;'
        f'color:{_INK};margin-top:5px;">{html.escape(title)}</div>'
        f"{subtitle_html}"
        "</td></tr></table>"
        f"{desc_html}"
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="margin-top:16px;border-top:1px solid #F0F0F0;"><tr>'
        f'<td style="padding-top:13px;vertical-align:middle;">{meta_html}</td>'
        '<td align="right" style="padding-top:13px;vertical-align:middle;white-space:nowrap;">'
        f'<span class="opp-cta" style="font-size:13.5px;font-weight:600;color:{_PURPLE};">'
        "View →</span>"
        "</td></tr></table>"
        "</td></tr></table></a>"
    )


def render_digest(
    company_name: str,
    matches: list[dict[str, Any]],
    settings: Settings,
    *,
    first_name: str = "",
) -> tuple[str, str, str]:
    """Return ``(subject, text_body, html_body)`` for a company's digest."""
    count = len(matches)
    who = company_name or "your organisation"
    noun = "opportunity" if count == 1 else "opportunities"
    subject = f"{count} new {noun} matched to {who}"

    planner_url = settings.client_portal_url or _PLANNER_FALLBACK
    unsubscribe = f"mailto:{settings.email_reply_to}?subject=Unsubscribe"
    try:
        tz = ZoneInfo(settings.scheduler_timezone)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    today = datetime.now(tz).date()
    week_of = f"{today.day} {_MONTHS[today.month - 1]}"

    # Per-match data, computed once and reused for the hero, cards, and text.
    cards_html: list[str] = []
    text_cards: list[str] = []
    titles: list[str] = []
    days_seen: list[int] = []
    ceiling_total = 0

    for i, match in enumerate(matches):
        fields = match.get("fields", {})
        raw = _parse_raw_json(fields.get("Raw Json"))
        gd = _parse_grant_details(fields.get("Grant Details JSON"))

        title = _card_title(fields, raw, gd)
        titles.append(title)
        match_type = _text(fields.get("Type")).strip() or _text(raw.get("Match Type")).strip()
        eligibility = (
            _text(raw.get("Match Level")).strip() or _text(raw.get("Eligibility Status")).strip()
        )
        eyebrow = _eyebrow(match_type, eligibility, _scope_label(gd))
        subtitle = _subtitle(gd)
        desc = _card_description(fields, raw)
        # Funding + deadline come from the grant's ready-made lookup fields
        # ("Grant Size", "Grant Deadline"); fall back to the parsed Grant Details
        # JSON only when a grant hasn't populated them.
        funding = _text(_lookup_first(fields.get("Grant Size"))).strip() or _funding_label(gd)
        date_str, days = _deadline_from_field(fields.get("Grant Deadline"), today)
        if not date_str:
            date_str, days = _deadline_and_days(gd, today)
        if days is not None:
            days_seen.append(days)
        ceiling_total += _max_ticket(gd)

        cards_html.append(
            _render_card(
                href=planner_url,
                eyebrow=eyebrow,
                title=title,
                subtitle=subtitle,
                desc=desc,
                funding=funding,
                date_str=date_str,
                days=days,
                last=(i == count - 1),
            )
        )

        line = [f"{i + 1}. {title}"]
        if eyebrow:
            line.append(f"   [{eyebrow}]")
        if subtitle:
            line.append(f"   {subtitle}")
        if desc:
            line.append(f"   {desc}")
        meta_text = " · ".join(
            p
            for p in (
                f"Funding {funding}" if funding else "",
                date_str + (f" ({days} days left)" if days is not None else "") if date_str else "",
            )
            if p
        )
        if meta_text:
            line.append(f"   {meta_text}")
        line.append(f"   {planner_url}")
        text_cards.append("\n".join(line))

    ceiling = f"€{_compact_amount(ceiling_total)}+" if ceiling_total else ""
    soonest = min(days_seen) if days_seen else None

    # --- hero intro (clauses dropped when their data is missing) ---
    greet_name = first_name.strip()
    if greet_name.startswith("#"):  # Airtable formula error (e.g. "#ERROR!")
        greet_name = ""
    greeting_html = f"Hi {html.escape(greet_name)} - " if greet_name else "Hi there - "
    verb = "is" if count == 1 else "are"
    intro_html = (
        greeting_html
        + "our team reviewed this week&#39;s open calls against your profile. "
        + f"{_count_word(count).capitalize()} {verb} worth your attention"
    )
    if ceiling:
        intro_html += (
            f', with a combined ceiling of <span style="color:{_INK};font-weight:500;">'
            f"{html.escape(ceiling)}</span>"
        )
    intro_html += "."
    if soonest is not None:
        intro_html += f" The soonest closes in {'1 day' if soonest == 1 else f'{soonest} days'}."

    cta_text = (
        "Open your match in the report →"
        if count == 1
        else f"Open all {count} matches in your report →"
    )

    preheader = (
        f"{_join_titles(titles)} just matched your profile."
        if titles
        else "New funding matches inside."
    )
    if ceiling:
        preheader += f" {ceiling} in reach."

    head = (
        '<!DOCTYPE html><html lang="en"><head>'
        '<meta charset="UTF-8" />'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0" />'
        '<meta name="x-apple-disable-message-reformatting" />'
        f"<title>{html.escape(subject)}</title>"
        '<link rel="preconnect" href="https://api.fontshare.com" crossorigin />'
        '<link href="https://api.fontshare.com/v2/css?f[]=satoshi@300,400,500,700,900&display=swap" '
        'rel="stylesheet" />'
        "<style>" + _STYLE + "</style>"
        "</head>"
    )
    body = (
        "<body>"
        '<div style="display:none;max-height:0;overflow:hidden;opacity:0;font-size:1px;'
        f'line-height:1px;color:#F2F2F2;">{html.escape(preheader)}</div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#F2F2F2;"><tr><td align="center" style="padding:32px 16px;">'
        '<table role="presentation" class="container" width="600" cellpadding="0" cellspacing="0" '
        'style="width:600px;max-width:600px;background:#FFFFFF;border:1px solid #E5E5E5;'
        f'border-radius:18px;overflow:hidden;font-family:{_FONT};">'
        # header / brand bar
        '<tr><td class="px" style="padding:22px 36px;border-bottom:1px solid #F0F0F0;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        f'<td align="left" style="vertical-align:middle;">{_LOGO_SVG}</td>'
        '<td align="right" style="vertical-align:middle;font-size:11px;font-weight:400;'
        f'letter-spacing:2.5px;text-transform:uppercase;color:{_MUTED};">Funding brief</td>'
        "</tr></table></td></tr>"
        # hero
        '<tr><td class="px" style="padding:36px 36px 26px 36px;">'
        '<div style="font-size:11px;font-weight:400;letter-spacing:2.5px;text-transform:uppercase;'
        f'color:{_MUTED};margin-bottom:16px;">New matches · Week of {html.escape(week_of)}</div>'
        '<h1 class="hero-h" style="margin:0;font-size:34px;line-height:1.1;font-weight:500;'
        f'letter-spacing:-0.9px;color:{_INK};">{count} new {noun} matched to '
        f'<span style="font-style:italic;color:{_PURPLE};">{html.escape(who)}</span></h1>'
        '<p style="margin:16px 0 0 0;font-size:15.5px;line-height:1.6;color:#525252;'
        f'font-weight:400;">{intro_html}</p>'
        "</td></tr>"
        # opportunity cards
        '<tr><td class="px" style="padding:0 36px;">' + "".join(cards_html) + "</td></tr>"
        # primary CTA
        '<tr><td class="px" style="padding:24px 36px 4px 36px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        f'<td align="center" style="border-radius:9px;background:{_PURPLE};">'
        f'<a href="{html.escape(planner_url)}" style="display:block;padding:15px 28px;'
        f'font-size:15px;font-weight:600;color:#FFFFFF;font-family:{_FONT};">'
        f"{html.escape(cta_text)}</a>"
        "</td></tr></table>"
        '<p style="margin:14px 0 0 0;font-size:13px;line-height:1.5;'
        f'color:{_MUTED};text-align:center;">Want us to start a concept note on one of these? '
        "Just reply - a senior reviewer reads every message.</p>"
        "</td></tr>"
        # footer
        '<tr><td class="px" style="padding:28px 36px 30px 36px;border-top:1px solid #F0F0F0;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        '<td style="vertical-align:middle;">'
        f'<div style="font-size:13px;font-weight:500;color:{_INK};">Cogrant</div>'
        f'<div style="font-size:12px;color:{_MUTED};margin-top:3px;">'
        "Your grants team - software-fast, human-reviewed.</div></td>"
        f'<td align="right" style="vertical-align:middle;font-size:12px;color:{_MUTED};">'
        f'<a href="https://cogrant.eu" style="color:{_PURPLE};">cogrant.eu</a></td>'
        "</tr></table>"
        '<p style="margin:18px 0 0 0;font-size:11px;line-height:1.5;color:#A3A3A3;">'
        f"You&#39;re receiving this because {html.escape(who)} has an active Cogrant OS profile. "
        "Matches are reviewed weekly. "
        f'<a href="{html.escape(planner_url)}" style="color:#A3A3A3;text-decoration:underline;">'
        "Manage alerts</a> · "
        f'<a href="{html.escape(unsubscribe)}" style="color:#A3A3A3;text-decoration:underline;">'
        "Unsubscribe</a></p>"
        "</td></tr>"
        "</table>"
        '<div style="font-size:11px;color:#A3A3A3;margin-top:18px;'
        f'font-family:{_FONT};">Cogrant · Vilnius, Lithuania</div>'
        "</td></tr></table></body></html>"
    )
    html_body = head + body

    text_greeting = f"Hi {greet_name}," if greet_name else "Hi there,"
    text_parts = [
        text_greeting,
        "",
        f"We reviewed this week's open calls against your profile. "
        f"{count} new {noun} for {who}:",
        "",
        *(c + "\n" for c in text_cards),
        f"{cta_text.rstrip(' →')}: {planner_url}",
        "",
        "Reply to this email and a senior reviewer will read it.",
        "",
        "— Cogrant · Vilnius, Lithuania",
    ]
    return subject, "\n".join(text_parts), html_body


def _join_titles(titles: list[str]) -> str:
    if len(titles) == 1:
        return titles[0]
    if len(titles) == 2:
        return f"{titles[0]} and {titles[1]}"
    if len(titles) == 3:
        return f"{titles[0]}, {titles[1]} and {titles[2]}"
    return f"{titles[0]}, {titles[1]} and {len(titles) - 2} more"


def _is_enabled(company_fields: dict[str, Any]) -> bool:
    return _text(company_fields.get("Notification Customer")) in _ENABLED_TIERS


class NotificationService:
    def __init__(
        self, repo: AirtableRepo, smtp: SmtpClient, settings: Settings
    ) -> None:
        self.repo = repo
        self.smtp = smtp
        self.settings = settings

    async def run_daily(self, *, dry_run: bool | None = None) -> dict[str, int]:
        """Send the pending-match digest. ``dry_run`` overrides ``email_dry_run``
        for this call (``None`` honours the global setting); returns a count summary."""
        dry_run_effective = (
            self.settings.email_dry_run if dry_run is None else dry_run
        )
        empty = {"companies": 0, "sent": 0, "skipped": 0, "failed": 0}
        if not self.settings.email_enabled:
            logger.info("notifications.skip reason=email_disabled")
            return empty

        matches = await asyncio.to_thread(self.repo.list_pending_notification_matches)
        if not matches:
            logger.info("notifications.none_pending")
            return empty

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
            first_name = _text(cfields.get("First name (šauksmininkas)")).strip()

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
                company_name, company_matches, self.settings, first_name=first_name
            )
            recipient = self.settings.email_from if dry_run_effective else email
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
        return {
            "companies": len(groups),
            "sent": sent,
            "skipped": skipped,
            "failed": failed,
        }

    async def _mark(self, matches: list[dict[str, Any]], status: str) -> None:
        for match in matches:
            await asyncio.to_thread(
                self.repo.set_match_notification_status, match["id"], status
            )
            await asyncio.sleep(0.2)  # stay under Airtable's 5 req/s
