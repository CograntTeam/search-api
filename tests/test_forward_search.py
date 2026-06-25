"""Tests for the in-process forward search (company → eligible grants → matches).

Uses the real filter funnel, gate, and write-back mapping with a fake repo and a
fake Gemini, exactly like the reverse-search tests.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.config import Settings
from app.services.forward_search import ForwardSearchService
from app.services.gemini_client import GeminiError

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

CLASSIFICATION = {
    "eligibility_basics": {"company_size_class": "Small", "date_of_establishment": "2015-01-01"},
    "activity_sectors": {"values": ["Software Product Development"]},
    "verticality": {"is_horizontal": True, "vertical_sectors_impacted": []},
    "beneficiary_archetype": {"values": ["Innovative SME"]},
    "subject_expertise": {"domains": []},
}


def _settings(**over: Any) -> Settings:
    base = dict(
        gemini_api_key="test-key",
        forward_search_concurrency=2,
        forward_search_max_grants=50,
        email_enabled=False,
    )
    base.update(over)
    return Settings(**base)


def _company(*, classified: bool = True, matches: list[str] | None = None) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "Company name": "Acme",
        "Country": "Lithuania",
        "City of Establishment": "",
        "Organisation Type": "Private Business",
        "Years of Establishment": "10",
        "Consortium Stance": "Consortium Optional",
        "Company description": "An AI software company.",
        "Company Preference Profile": "",
        "Search Matches": matches or [],
    }
    if classified:
        fields.update(
            {
                "Activity Sectors": ["Software Product Development"],
                "Application Area": ["Horizontal"],
                "Company Size Class": "Small",
                "Acceptable Instruments": ["Grant - Lump Sum"],
                "Organisation Archetype": "Innovative SME",
                "Subject Expertise": [],
            }
        )
    return {"id": "recCO1", "fields": fields}


def _grant(grant_id: str = "recG1") -> dict[str, Any]:
    return {
        "id": grant_id,
        "fields": {
            "Name": "Test Grant",
            "Grant Description": "International R&D for SMEs.",
            "Grant Details": "Up to 500k.",
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
        },
    }


class FakeGemini:
    def __init__(
        self,
        *,
        decision: dict | None = None,
        classification: dict | None = None,
        cache_name: str | None = None,
    ) -> None:
        self.decision = decision or PASS_DECISION
        self.classification = classification or CLASSIFICATION
        self.cache_name = cache_name  # None → caching unavailable → fallback path
        self.classify_calls = 0
        self.sanity_calls = 0
        self.cached_calls = 0
        self.caches_created = 0
        self.deleted_caches: list[str] = []

    async def classify_company(self, *, company_description, today):  # noqa: ANN001
        self.classify_calls += 1
        return self.classification, {"prompt": 1, "candidates": 1, "total": 2, "cached": 0}

    async def sanity_check(self, *, today, company_description, grant_name, grant_description):  # noqa: ANN001
        self.sanity_calls += 1
        return self.decision, {"prompt": 3, "candidates": 4, "total": 7, "cached": 0}

    async def create_sanity_cache(self, *, today, company_description):  # noqa: ANN001
        if self.cache_name:
            self.caches_created += 1
        return self.cache_name

    async def sanity_check_cached(self, *, cache_name, grant_name, grant_description):  # noqa: ANN001
        self.cached_calls += 1
        return self.decision, {"prompt": 1, "candidates": 4, "total": 5, "cached": 2}

    async def delete_cache(self, cache_name):  # noqa: ANN001
        self.deleted_caches.append(cache_name)


class FakeRepo:
    def __init__(self, company, grants, *, already: set[str] | None = None) -> None:
        self.company = company
        self.grants = grants
        self.already = already or set()
        self.statuses: list[str] = []
        self.updates: list[dict[str, Any]] = []
        self.created: list[dict[str, Any]] = []

    def get_company_for_search(self, company_id):
        return self.company if self.company and self.company["id"] == company_id else None

    def update_company_fields(self, company_id, fields, *, typecast=True):  # noqa: ANN001
        self.updates.append(fields)
        if "Grant Search Status" in fields:
            self.statuses.append(fields["Grant Search Status"])
        self.company["fields"].update(fields)
        return {"id": company_id, "fields": dict(self.company["fields"])}

    def list_grants_for_filtering(self):
        return list(self.grants)

    def grant_ids_with_existing_matches(self, match_ids):
        return set(self.already)

    def create_search_match(self, fields):
        self.created.append(fields)
        return f"recM{len(self.created)}"


async def test_classified_company_creates_match():
    repo = FakeRepo(_company(classified=True), [_grant()])
    gem = FakeGemini()
    job = uuid4()
    summary = await ForwardSearchService(repo, gem, _settings()).run_for_company(
        "recCO1", api_job_id=job
    )
    assert gem.classify_calls == 0  # already classified
    assert summary["matches_created"] == 1
    assert len(repo.created) == 1
    match = repo.created[0]
    assert match["Notification Status"] == "Skipped"
    assert match["api_job_id"] == str(job)
    assert match["Grant"] == ["recG1"]
    assert match["Company"] == ["recCO1"]
    assert repo.statuses == ["Search in Progress", "Search Completed"]


async def test_unclassified_company_is_classified_first():
    # No grants → no matches, so the test isolates the classification write-back.
    repo = FakeRepo(_company(classified=False), [])
    gem = FakeGemini()
    summary = await ForwardSearchService(repo, gem, _settings()).run_for_company(
        "recCO1", api_job_id=uuid4()
    )
    assert gem.classify_calls == 1
    written = {k: v for u in repo.updates for k, v in u.items()}
    assert written["Activity Sectors"] == ["Software Product Development"]
    assert written["Consortium Stance"] == "Consortium Optional"
    assert "Company Classification Profile" in written["Company description"]
    assert summary["matches_created"] == 0
    assert repo.statuses == ["Search in Progress", "Search Completed"]


async def test_dedup_skips_already_matched_grants():
    repo = FakeRepo(
        _company(classified=True, matches=["recM_existing"]),
        [_grant("recG1")],
        already={"recG1"},
    )
    gem = FakeGemini()
    summary = await ForwardSearchService(repo, gem, _settings()).run_for_company(
        "recCO1", api_job_id=uuid4()
    )
    assert summary["deduped"] == 1
    assert summary["matches_created"] == 0
    assert gem.sanity_calls == 0
    assert repo.created == []


async def test_fail_verdict_creates_no_match():
    repo = FakeRepo(_company(classified=True), [_grant()])
    gem = FakeGemini(decision={**PASS_DECISION, "hard_requirement_match_verdict": "Not Eligible"})
    summary = await ForwardSearchService(repo, gem, _settings()).run_for_company(
        "recCO1", api_job_id=uuid4()
    )
    assert summary["evaluated"] == 1
    assert summary["matches_created"] == 0
    assert summary["dismissed"] == 1
    assert repo.created == []


async def test_missing_gemini_key_raises():
    repo = FakeRepo(_company(), [_grant()])
    with pytest.raises(GeminiError):
        await ForwardSearchService(
            repo, FakeGemini(), _settings(gemini_api_key=None)
        ).run_for_company("recCO1", api_job_id=uuid4())


async def test_uses_context_cache_when_available():
    repo = FakeRepo(_company(classified=True), [_grant("recG1"), _grant("recG2")])
    gem = FakeGemini(cache_name="cachedContents/abc123")
    summary = await ForwardSearchService(repo, gem, _settings()).run_for_company(
        "recCO1", api_job_id=uuid4()
    )
    # One cache for the run; every grant goes through the cached path, never the
    # full-prompt fallback.
    assert gem.caches_created == 1
    assert gem.cached_calls == 2
    assert gem.sanity_calls == 0
    # The cache is cleaned up when the run ends.
    assert gem.deleted_caches == ["cachedContents/abc123"]
    # Cached-token usage is surfaced in the job result.
    assert summary["tokens"]["cached"] == 4  # 2 grants × 2 cached tokens each
    assert summary["matches_created"] == 2


async def test_falls_back_to_full_prompt_without_cache():
    repo = FakeRepo(_company(classified=True), [_grant()])
    gem = FakeGemini(cache_name=None)  # caching unavailable
    summary = await ForwardSearchService(repo, gem, _settings()).run_for_company(
        "recCO1", api_job_id=uuid4()
    )
    assert gem.sanity_calls == 1
    assert gem.cached_calls == 0
    assert gem.caches_created == 0
    assert gem.deleted_caches == []
    assert summary["matches_created"] == 1


def test_sanity_prompt_split_is_cache_friendly():
    from app.services.prompts import (
        SANITY_CHECK_INSTRUCTIONS,
        build_sanity_check_call_block,
        build_sanity_check_static_prefix,
    )

    prefix = build_sanity_check_static_prefix(
        today="2026-06-25", company_description="ACME builds AI tooling."
    )
    call = build_sanity_check_call_block(
        grant_name="Eurostars Call 11", grant_description="R&D grant for SMEs."
    )
    # Rubric + company live in the cacheable prefix; the grant does NOT (otherwise
    # the cache key would change per grant and never hit).
    assert prefix.startswith(SANITY_CHECK_INSTRUCTIONS)
    assert "ACME builds AI tooling." in prefix
    assert "Eurostars Call 11" not in prefix
    # Only the grant lives in the per-call tail; the rubric is never re-sent.
    assert "Eurostars Call 11" in call and "R&D grant for SMEs." in call
    assert SANITY_CHECK_INSTRUCTIONS not in call
