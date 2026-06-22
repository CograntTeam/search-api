"""Tests for the reverse-search orchestrator (filters + gate + write-back wiring)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.config import Settings
from app.services.reverse_search import ReverseSearchService

PASS_DECISION = {
    "Hook": "Scale across Europe.",
    "Match summary": "Strong candidate.",
    "hard_requirement_match_verdict": "Eligible",
    "eligibility_criteria": [{"criteria": "Is an SME", "status": "Pass"}],
    "objective_fit_status": "Strong fit",
    "activity_fit_status": "Strong fit",
    "budget_fit_status": "Strong fit",
    "capacity_fit_status": "Likely fit",
    "summa_summarum_objective": "o",
    "summa_summarum_activity": "a",
    "summa_summarum_budget": "b",
    "consortium": {"required": "Optional", "potential_role": "Partner", "missing_partner_roles": []},
}
FAIL_DECISION = {**PASS_DECISION, "hard_requirement_match_verdict": "Not Eligible"}


def _settings(**overrides: Any) -> Settings:
    base = dict(gemini_api_key="test-key", reverse_search_concurrency=2, email_enabled=False)
    base.update(overrides)
    return Settings(**base)


def _grant(deadline: str = "2099-01-01", scrape: str = "Data Enriched", matches=None) -> dict[str, Any]:
    return {
        "id": "recG1",
        "fields": {
            "Name": "Eurostars Call 11",
            "Grant Description": "International R&D for SMEs.",
            "Grant Details": "Up to 500k.",
            "Reverse Search Status": "Idle",
            "Scrape Status": scrape,
            "Application Deadline": deadline,
            "Grant Geography": "European",
            "Eligible Country": "[]",
            "Eligible City": "[]",
            "Eligible Types of Organisation": ["Private Business"],
            "Supported Sector": "Agriculture and Food, Software and IT Services",
            "Application Areas": "Horizontal",
            "Size Classes": ["Micro", "Small", "Medium"],
            "Minimum Years Established": "",
            "Maximum Years Established": "",
            "Support Forms": "Grant - Actual Costs",
            "Organisation Archetypes": "SME",
            "Required Subject Expertise": "",
            "Consortium Possibility": "Consortium Optional",
            "Search Matches": matches or [],
        },
    }


def _company(cid: str) -> dict[str, Any]:
    return {
        "id": cid,
        "fields": {
            "Company name": cid.upper(),
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
            "Company description": "We do regenerative agriculture.",
            "Company Preference Profile": "Looking for R&D grants.",
        },
    }


class FakeGemini:
    def __init__(self, decision: dict[str, Any]) -> None:
        self.decision = decision
        self.calls: list[str] = []

    async def sanity_check(self, *, today, company_description, grant_name, grant_description):
        self.calls.append(company_description)
        return dict(self.decision), {"prompt": 10, "candidates": 5, "total": 15}


class FakeRepo:
    def __init__(self, grants, companies, existing_company_ids=None) -> None:
        self._grants = grants
        self._companies = companies
        self._existing = set(existing_company_ids or [])
        self.status_updates: list[dict[str, Any]] = []
        self.created: list[dict[str, Any]] = []
        self.companies_fetched = 0

    def list_idle_grants(self):
        return [dict(g) for g in self._grants]

    def list_companies_for_filtering(self):
        self.companies_fetched += 1
        return [dict(c) for c in self._companies]

    def update_grant_reverse_search(self, grant_id, *, status, log=None):
        self.status_updates.append({"id": grant_id, "status": status, "log": log})

    def create_search_match(self, fields):
        self.created.append(fields)
        return f"recNEW{len(self.created)}"

    def company_ids_with_existing_matches(self, match_ids):
        return set(self._existing)

    def _statuses(self, grant_id: str) -> list[str]:
        return [u["status"] for u in self.status_updates if u["id"] == grant_id]


async def test_pass_creates_matches_and_completes():
    repo = FakeRepo([_grant()], [_company("c1"), _company("c2")])
    gemini = FakeGemini(PASS_DECISION)
    await ReverseSearchService(repo, gemini, _settings()).run_once()

    assert len(repo.created) == 2
    # The budget fix flows through end to end.
    assert all(f["Budget Fit Status"] == "Strong fit" for f in repo.created)
    assert all(f["Notification Status"] == "Pending" for f in repo.created)
    assert repo._statuses("recG1") == ["In Progress", "Completed"]
    log = repo.status_updates[-1]["log"]
    assert "Companies reviewed: 2" in log
    assert "2 matches created" in log


async def test_fail_creates_no_matches():
    repo = FakeRepo([_grant()], [_company("c1"), _company("c2")])
    await ReverseSearchService(repo, FakeGemini(FAIL_DECISION), _settings()).run_once()
    assert repo.created == []
    assert repo._statuses("recG1")[-1] == "Completed"
    assert "0 matches created" in repo.status_updates[-1]["log"]
    assert "2 dismissed" in repo.status_updates[-1]["log"]


async def test_not_enriched_grant_left_idle():
    repo = FakeRepo([_grant(scrape="Running")], [_company("c1")])
    await ReverseSearchService(repo, FakeGemini(PASS_DECISION), _settings()).run_once()
    assert repo.status_updates == []  # untouched, stays Idle
    assert repo.created == []
    assert repo.companies_fetched == 0


async def test_deadline_soon_marked_completed_and_skipped():
    soon = (date.today() + timedelta(days=3)).isoformat()
    repo = FakeRepo([_grant(deadline=soon)], [_company("c1")])
    await ReverseSearchService(repo, FakeGemini(PASS_DECISION), _settings()).run_once()
    assert repo.created == []
    assert repo.companies_fetched == 0
    assert repo._statuses("recG1") == ["Completed"]
    assert "SKIPPED" in repo.status_updates[-1]["log"]


async def test_dedup_skips_already_matched_company():
    repo = FakeRepo(
        [_grant(matches=["recExistingMatch"])],
        [_company("c1"), _company("c2")],
        existing_company_ids={"c1"},
    )
    gemini = FakeGemini(PASS_DECISION)
    await ReverseSearchService(repo, gemini, _settings()).run_once()
    assert len(repo.created) == 1
    assert repo.created[0]["Company"] == ["c2"]
    assert len(gemini.calls) == 1  # c1 never reaches the LLM
    assert "Skipped (already matched): 1" in repo.status_updates[-1]["log"]
