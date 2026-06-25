"""Forward funnel: filter GRANTS for one company (inverse of run_filter_funnel)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.services.match_filters import run_company_funnel

# A company that satisfies every clause for the baseline grant below.
COMPANY: dict[str, Any] = {
    "Country": "Lithuania",
    "City of Establishment": "",
    "Organisation Type": "Private Business",
    "Activity Sectors": ["Software Product Development"],
    "Application Area": ["Horizontal"],
    "Company Size Class": "Small",
    "Years of Establishment": "10",
    "Acceptable Instruments": ["Grant - Lump Sum"],
    "Organisation Archetype": "Innovative SME",
    "Subject Expertise": [],
    "Consortium Stance": "Consortium Optional",
}


def _grant(grant_id: str, **over: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "Name": "G",
        "Scrape Status": "Data Enriched",
        "Application Deadline": "2099-01-01",
        "Grant Geography": "European",
        "Eligible Country": "[]",
        "Eligible City": "[]",
        "Eligible Types of Organisation": ["Private Business"],
        "Supported Sector": "Software Product Development",
        "Application Areas": "Horizontal",
        "Size Classes": ["Small"],
        "Minimum Years Established": "",
        "Maximum Years Established": "",
        "Support Forms": "Grant - Lump Sum",
        "Organisation Archetypes": "Innovative SME",
        "Required Subject Expertise": "",
        "Consortium Possibility": "Consortium Optional",
    }
    fields.update(over)
    return {"id": grant_id, "fields": fields}


def test_eligible_grant_passes_every_clause():
    funnel = run_company_funnel([_grant("recG1")], COMPANY)
    assert funnel.reviewed == 1
    assert [g["id"] for g in funnel.eligible] == ["recG1"]


def test_sector_mismatch_is_dropped():
    funnel = run_company_funnel(
        [_grant("recG2", **{"Supported Sector": "Agriculture and Food"})], COMPANY
    )
    assert funnel.eligible == []
    sector = next(s for s in funnel.stages if s.name == "sector")
    assert sector.dropped == 1


def test_not_enriched_dropped_by_precondition():
    funnel = run_company_funnel([_grant("recG3", **{"Scrape Status": "Scraping"})], COMPANY)
    assert funnel.eligible == []
    assert funnel.stages[0].name == "enrichment + deadline"
    assert funnel.stages[0].dropped == 1


def test_soon_deadline_dropped_by_precondition():
    soon = (date.today() + timedelta(days=3)).isoformat()
    funnel = run_company_funnel(
        [_grant("recG4", **{"Application Deadline": soon})], COMPANY
    )
    assert funnel.eligible == []


def test_mixed_set_keeps_only_eligible():
    grants = [
        _grant("recOK"),
        _grant("recBadSector", **{"Supported Sector": "Construction and Infrastructure"}),
        _grant("recBadType", **{"Eligible Types of Organisation": ["Research Institution"]}),
    ]
    funnel = run_company_funnel(grants, COMPANY)
    assert [g["id"] for g in funnel.eligible] == ["recOK"]
    assert funnel.reviewed == 3
