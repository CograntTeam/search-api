"""Unit tests for the deterministic company<->grant filters + the funnel."""

from __future__ import annotations

from datetime import date

from app.services.match_filters import grant_precondition, run_filter_funnel

GRANT = {
    "Grant Geography": "European",
    "Eligible Country": "[]",
    "Eligible City": "[]",
    "Eligible Types of Organisation": ["Private Business", "Non-profit"],
    "Supported Sector": "Agriculture and Food, Software and IT Services",
    "Application Areas": "Horizontal",
    "Size Classes": ["Micro", "Small", "Medium"],
    "Minimum Years Established": "",
    "Maximum Years Established": "",
    "Support Forms": "Grant - Actual Costs, Loan",
    "Organisation Archetypes": "SME, Startup",
    "Required Subject Expertise": "",
    "Consortium Possibility": "Consortium Optional",
    "Scrape Status": "Data Enriched",
    "Application Deadline": "2099-01-01",
}


def _company(cid, **fields):
    base = {
        "Country": "Lithuania",
        "City of Establishment": "",
        "Organisation Type": "Private Business",
        "Activity Sectors": ["Agriculture and Food"],
        "Application Area": ["Renewable energy"],
        "Company Size Class": "Small",
        "Years of Establishment": "3",
        "Acceptable Instruments": ["Grant - Actual Costs"],
        "Organisation Archetype": "SME",
        "Subject Expertise": [],
        "Consortium Stance": "Consortium Optional",
    }
    base.update(fields)
    return {"id": cid, "fields": base}


def test_fully_eligible_company_passes_every_clause():
    funnel = run_filter_funnel([_company("c1")], GRANT)
    assert funnel.reviewed == 1
    assert funnel.eligible_count == 1
    assert funnel.eligible[0]["id"] == "c1"


def test_funnel_attributes_drops_to_the_right_stage():
    companies = [
        _company("c1"),  # passes
        _company("c2", **{"Organisation Type": "Government"}),  # drops at org type
        _company("c3", **{"Activity Sectors": ["Mining"]}),  # drops at sector
    ]
    funnel = run_filter_funnel(companies, GRANT)
    assert funnel.reviewed == 3
    assert funnel.eligible_count == 1
    assert funnel.eligible[0]["id"] == "c1"
    drops = {s.name: s.dropped for s in funnel.stages}
    assert drops["organisation type"] == 1
    assert drops["sector"] == 1
    assert drops["geography"] == 0


def test_non_european_company_needs_country_match():
    grant = {**GRANT, "Grant Geography": "National", "Eligible Country": "Lithuania, Latvia"}
    inside = run_filter_funnel([_company("c1", Country="Lithuania")], grant)
    outside = run_filter_funnel([_company("c2", Country="Spain")], grant)
    assert inside.eligible_count == 1
    assert outside.eligible_count == 0


def test_year_bounds_enforced():
    grant = {**GRANT, "Minimum Years Established": "5", "Maximum Years Established": "20"}
    too_young = run_filter_funnel([_company("c1", **{"Years of Establishment": "2"})], grant)
    ok = run_filter_funnel([_company("c2", **{"Years of Establishment": "8"})], grant)
    assert too_young.eligible_count == 0
    assert ok.eligible_count == 1


def test_consortium_stance_compatibility():
    grant = {**GRANT, "Consortium Possibility": "Consortium Required"}
    mono = run_filter_funnel([_company("c1", **{"Consortium Stance": "Mono-beneficiary Only"})], grant)
    optional = run_filter_funnel([_company("c2", **{"Consortium Stance": "Consortium Optional"})], grant)
    assert mono.eligible_count == 0  # mono can't do a consortium-required call
    assert optional.eligible_count == 1


# ---------------------------------------------------------------------------
# grant-level preconditions
# ---------------------------------------------------------------------------
def test_precondition_ok():
    assert grant_precondition(GRANT, today=date(2026, 6, 22)) is None


def test_precondition_not_enriched():
    assert grant_precondition({**GRANT, "Scrape Status": "Running"}) == "not enriched"


def test_precondition_deadline_within_a_week():
    soon = {**GRANT, "Application Deadline": "2026-06-25"}
    reason = grant_precondition(soon, today=date(2026, 6, 22))
    assert reason == "deadline within 7 days"


def test_precondition_blank_deadline_allowed():
    assert grant_precondition({**GRANT, "Application Deadline": ""}, today=date(2026, 6, 22)) is None
