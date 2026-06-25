"""Deterministic company <-> grant hard filters for the reverse search.

Ported from the canonical (maintained, forward) ``Get Initial Grant List2``
``filterByFormula`` and verified against the live Airtable schema. The n8n does
it as one server-side formula; we apply the clauses sequentially in Python so we
can report the per-filter funnel (how many companies survive each step) onto the
grant's ``Reverse Search Log``.

The funnel begins with a Cogrant business gate — only ``Notification Customer = Pro``
companies are checked — then applies the 12 ported eligibility clauses below.

Airtable formula semantics we reproduce:
* ``FIND(needle, haystack)`` is truthy when ``needle`` is empty or a substring of
  ``haystack`` (Airtable returns 1 for an empty needle).
* ``REGEX_MATCH(text, '(a|b|c)')`` — the alternation is built from a (possibly
  multi-value) company field; an empty value list yields ``()`` which matches
  anything, exactly as Airtable does.
* ``VALUE(x)`` extracts the leading number.
* multipleSelects fields arrive from pyairtable as lists; singleSelect/text as
  strings; blank cells are absent. We coerce defensively.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Grants must be fully enriched before they carry the structured fields the
# filters and the LLM rely on.
SCRAPE_STATUS_READY = "Data Enriched"
# Skip grants whose deadline is sooner than this — no point notifying a client
# about a call that closes within the week.
DEADLINE_MIN_DAYS = 7
# Only notification-enabled companies are worth a reverse search (only they get
# emailed). Must stay in sync with the email gate in app/services/notifications.py
# (_ENABLED_TIERS). Hardcoded to Pro per product decision.
NOTIFICATION_ENABLED_TIER = "Pro"


# ---------------------------------------------------------------------------
# value coercion
# ---------------------------------------------------------------------------
def _text(value: Any) -> str:
    """Coerce an Airtable cell to text the way a formula would see it."""
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _list(value: Any) -> list[str]:
    """Coerce an Airtable cell to a list of non-empty string values."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v) != ""]
    return [str(value)]


def _blank(value: Any) -> bool:
    """True for cells Airtable would treat as empty (incl. the literal "[]")."""
    return _text(value).strip() in ("", "[]")


def _find(needle: str, haystack: str) -> bool:
    """Airtable ``FIND`` truthiness: empty needle matches; else substring."""
    return needle == "" or needle in haystack


def _regex_match(text: str, values: Sequence[str]) -> bool:
    """Airtable ``REGEX_MATCH(text, '(' & join(values, '|') & ')')``.

    An empty ``values`` list produces ``()`` which matches anything. If the
    assembled pattern is not valid regex we fall back to a literal OR-substring
    test so a stray metacharacter never crashes a run.
    """
    pattern = "(" + "|".join(values) + ")"
    try:
        return re.search(pattern, text) is not None
    except re.error:
        return any(v and v in text for v in values)


def _value(value: Any) -> float | None:
    """Airtable ``VALUE`` — first number found, else None."""
    m = re.search(r"-?\d+(?:\.\d+)?", _text(value))
    return float(m.group()) if m else None


def _parse_date(value: Any) -> date | None:
    raw = _text(value).strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None


# ---------------------------------------------------------------------------
# per-company clauses (grant is fixed; each returns True = company still in)
# ---------------------------------------------------------------------------
def _geography(c: Mapping[str, Any], g: Mapping[str, Any]) -> bool:
    return _text(g.get("Grant Geography")) == "European" or _find(
        _text(c.get("Country")), _text(g.get("Eligible Country"))
    )


def _city(c: Mapping[str, Any], g: Mapping[str, Any]) -> bool:
    if _blank(g.get("Eligible City")):
        return True
    company_city = _text(c.get("City of Establishment"))
    return company_city == "" or _find(company_city, _text(g.get("Eligible City")))


def _org_type(c: Mapping[str, Any], g: Mapping[str, Any]) -> bool:
    return _find(
        _text(c.get("Organisation Type")),
        _text(g.get("Eligible Types of Organisation")),
    )


def _sector(c: Mapping[str, Any], g: Mapping[str, Any]) -> bool:
    return _regex_match(_text(g.get("Supported Sector")), _list(c.get("Activity Sectors")))


def _application_area(c: Mapping[str, Any], g: Mapping[str, Any]) -> bool:
    areas = _text(g.get("Application Areas"))
    return _regex_match(areas, ["Horizontal"]) or _regex_match(
        areas, _list(c.get("Application Area"))
    )


def _size_class(c: Mapping[str, Any], g: Mapping[str, Any]) -> bool:
    return _find(_text(c.get("Company Size Class")), _text(g.get("Size Classes")))


def _min_years(c: Mapping[str, Any], g: Mapping[str, Any]) -> bool:
    if _blank(g.get("Minimum Years Established")):
        return True
    years = _value(c.get("Years of Establishment"))
    minimum = _value(g.get("Minimum Years Established"))
    return years is not None and minimum is not None and years >= minimum


def _max_years(c: Mapping[str, Any], g: Mapping[str, Any]) -> bool:
    if _blank(g.get("Maximum Years Established")):
        return True
    years = _value(c.get("Years of Establishment"))
    maximum = _value(g.get("Maximum Years Established"))
    return years is not None and maximum is not None and years <= maximum


def _support_forms(c: Mapping[str, Any], g: Mapping[str, Any]) -> bool:
    return _regex_match(
        _text(g.get("Support Forms")), _list(c.get("Acceptable Instruments"))
    )


def _archetype(c: Mapping[str, Any], g: Mapping[str, Any]) -> bool:
    return _find(
        _text(c.get("Organisation Archetype")),
        _text(g.get("Organisation Archetypes")),
    )


def _subject_expertise(c: Mapping[str, Any], g: Mapping[str, Any]) -> bool:
    required = _text(g.get("Required Subject Expertise")).strip()
    if required == "":
        return True
    expertise = _list(c.get("Subject Expertise"))
    return bool(expertise) and _regex_match(_text(g.get("Required Subject Expertise")), expertise)


def _consortium(c: Mapping[str, Any], g: Mapping[str, Any]) -> bool:
    stance = _text(c.get("Consortium Stance"))
    possibility = _text(g.get("Consortium Possibility"))
    return (
        stance == "Consortium Optional"
        or (stance == "Mono-beneficiary Only" and possibility != "Consortium Required")
        or (stance == "Consortium Required" and possibility != "Mono-beneficiary Only")
    )


# Order matters: this is the funnel order reported in the log.
ClauseFn = Callable[[Mapping[str, Any], Mapping[str, Any]], bool]
CLAUSES: list[tuple[str, ClauseFn]] = [
    ("geography", _geography),
    ("city", _city),
    ("organisation type", _org_type),
    ("sector", _sector),
    ("application area", _application_area),
    ("size class", _size_class),
    ("min years established", _min_years),
    ("max years established", _max_years),
    ("support forms", _support_forms),
    ("organisation archetype", _archetype),
    ("subject expertise", _subject_expertise),
    ("consortium stance", _consortium),
]


# ---------------------------------------------------------------------------
# grant-level preconditions
# ---------------------------------------------------------------------------
def grant_precondition(grant_fields: Mapping[str, Any], *, today: date | None = None) -> str | None:
    """Return a skip-reason string if the grant should not be processed now,
    else ``None``.

    * not enriched  -> retry later (caller leaves it Queued)
    * deadline soon -> permanent skip (caller marks it Completed)
    """
    today = today or date.today()
    if _text(grant_fields.get("Scrape Status")) != SCRAPE_STATUS_READY:
        return "not enriched"
    deadline = _parse_date(grant_fields.get("Application Deadline"))
    if deadline is not None and deadline <= today + timedelta(days=DEADLINE_MIN_DAYS):
        return "deadline within 7 days"
    return None


# ---------------------------------------------------------------------------
# funnel
# ---------------------------------------------------------------------------
@dataclass
class FunnelStage:
    name: str
    dropped: int
    remaining: int


@dataclass
class FilterFunnel:
    reviewed: int = 0
    stages: list[FunnelStage] = field(default_factory=list)
    eligible: list[dict[str, Any]] = field(default_factory=list)

    @property
    def eligible_count(self) -> int:
        return len(self.eligible)


def run_filter_funnel(
    companies: Iterable[dict[str, Any]], grant_fields: Mapping[str, Any]
) -> FilterFunnel:
    """Apply each clause to the shrinking survivor set, recording drop-off.

    ``companies`` are Airtable-shaped rows: ``{"id": "rec...", "fields": {...}}``.
    """
    survivors = list(companies)
    funnel = FilterFunnel(reviewed=len(survivors))

    # Cogrant gate (runs before the ported eligibility clauses): only
    # notification-enabled companies are checked, since only they are emailed.
    kept = [
        rec
        for rec in survivors
        if _text(rec.get("fields", {}).get("Notification Customer")) == NOTIFICATION_ENABLED_TIER
    ]
    funnel.stages.append(
        FunnelStage("notification tier", dropped=len(survivors) - len(kept), remaining=len(kept))
    )
    survivors = kept

    for name, fn in CLAUSES:
        kept: list[dict[str, Any]] = []
        dropped = 0
        for rec in survivors:
            try:
                passed = fn(rec.get("fields", {}), grant_fields)
            except Exception:  # noqa: BLE001 — one bad row must not kill the run
                logger.warning(
                    "filter.error clause=%s company=%s", name, rec.get("id")
                )
                passed = False
            if passed:
                kept.append(rec)
            else:
                dropped += 1
        funnel.stages.append(FunnelStage(name=name, dropped=dropped, remaining=len(kept)))
        survivors = kept
    funnel.eligible = survivors
    return funnel


def run_company_funnel(
    grants: Iterable[dict[str, Any]],
    company_fields: Mapping[str, Any],
    *,
    today: date | None = None,
) -> FilterFunnel:
    """Forward search: filter GRANTS down to those one company is eligible for.

    The inverse of :func:`run_filter_funnel`. The 12 clauses are symmetric
    ``(company_fields, grant_fields) -> bool`` tests, so we reuse them verbatim
    with ``company_fields`` fixed and iterate over grants. There is **no** Pro
    "notification tier" gate — a partner-API search runs for the requesting
    company regardless of tier. The first stage applies ``grant_precondition``
    (enrichment + deadline), mirroring the n8n ``Get Initial Grant List2`` filter.

    ``grants`` are Airtable-shaped rows: ``{"id": "rec...", "fields": {...}}``.
    """
    survivors = list(grants)
    funnel = FilterFunnel(reviewed=len(survivors))

    kept = [g for g in survivors if grant_precondition(g.get("fields", {}), today=today) is None]
    funnel.stages.append(
        FunnelStage("enrichment + deadline", dropped=len(survivors) - len(kept), remaining=len(kept))
    )
    survivors = kept

    for name, fn in CLAUSES:
        kept = []
        dropped = 0
        for g in survivors:
            try:
                passed = fn(company_fields, g.get("fields", {}))
            except Exception:  # noqa: BLE001 — one bad row must not kill the run
                logger.warning("filter.error clause=%s grant=%s", name, g.get("id"))
                passed = False
            if passed:
                kept.append(g)
            else:
                dropped += 1
        funnel.stages.append(FunnelStage(name=name, dropped=dropped, remaining=len(kept)))
        survivors = kept
    funnel.eligible = survivors
    return funnel
