"""Unit tests for the sanity-check write-back: gate, flatten, field mapping."""

from __future__ import annotations

import json

from app.services.match_mapping import (
    build_search_match_fields,
    evaluate_gate,
    flatten_decision,
)


def _decision(**overrides):
    base = {
        "Hook": "Scale your model across Europe.",
        "Match summary": "A strong candidate for this call.",
        "hard_requirement_match_verdict": "Eligible",
        "eligibility_criteria": [{"criteria": "Is an SME", "status": "Pass"}],
        "priority_eligibility_criteria": [],
        "eligibility_clarification_questions": [],
        "objective_fit_status": "Strong fit",
        "summa_summarum_objective": "Objectives align well.",
        "objective_upgrade_question": [],
        "activity_fit_status": "Strong fit",
        "summa_summarum_activity": "Activities match.",
        "activity_upgrade_question": [],
        "budget_fit_status": "Likely fit",
        "summa_summarum_budget": "Budget is plausible.",
        "capacity_fit_status": "Likely fit",
        "summa_summarum_capacity": "Can deliver.",
        "capacity_upgrade_question": [],
        "consortium": {
            "required": "Optional",
            "potential_role": "Partner",
            "missing_partner_roles": ["validation site"],
        },
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# gate
# ---------------------------------------------------------------------------
def test_gate_passes_clean_decision():
    assert evaluate_gate(_decision()).passed


def test_gate_fails_on_not_eligible():
    result = evaluate_gate(_decision(hard_requirement_match_verdict="Not Eligible"))
    assert not result.passed
    assert any("Not Eligible" in r for r in result.reasons)


def test_gate_fails_on_dealbreaker():
    d = _decision(eligibility_criteria=[{"criteria": "Must be French", "status": "Dealbreaker"}])
    assert not evaluate_gate(d).passed


def test_gate_fails_on_any_no_fit():
    assert not evaluate_gate(_decision(activity_fit_status="No fit")).passed


def test_gate_allows_single_unlikely_fit_but_not_two():
    assert evaluate_gate(_decision(budget_fit_status="Unlikely fit")).passed
    two = _decision(budget_fit_status="Unlikely fit", capacity_fit_status="Unlikely fit")
    assert not evaluate_gate(two).passed


# ---------------------------------------------------------------------------
# flatten + write-back mapping
# ---------------------------------------------------------------------------
def test_budget_fit_status_uses_budget_not_objective():
    """The whole point of the fix: Budget Fit Status reflects the budget verdict."""
    d = _decision(objective_fit_status="Likely fit", budget_fit_status="Strong fit")
    fields = build_search_match_fields(d, grant_id="recG", company_id="recC")
    assert fields["Budget Fit Status"] == "Strong fit"
    assert fields["Objective Fit Status"] == "Likely fit"


def test_links_and_notification_status():
    fields = build_search_match_fields(_decision(), grant_id="recG", company_id="recC")
    assert fields["Grant"] == ["recG"]
    assert fields["Company"] == ["recC"]
    assert fields["Notification Status"] == "Pending"
    assert fields["api_job_id"] == ""


def test_unknown_fit_value_coerced_to_unclear():
    fields = build_search_match_fields(
        _decision(budget_fit_status="wildly off-script"),
        grant_id="recG",
        company_id="recC",
    )
    assert fields["Budget Fit Status"] == "Unclear"


def test_match_description_and_raw_json_roundtrip():
    fields = build_search_match_fields(_decision(), grant_id="recG", company_id="recC")
    assert fields["Match Description"].startswith("Scale your model")
    raw = json.loads(fields["Raw Json"])
    assert raw["Budget Fit Level"] == "Likely fit"
    assert raw["Objectives Fit Level"] == "Strong fit"
    # Missing-partner-roles is stored as a JSON string.
    assert json.loads(fields["Missing Partner Roles"]) == ["validation site"]


def test_flatten_builds_formatted_helpers():
    flat = flatten_decision(
        _decision(
            eligibility_clarification_questions=[{"question": "Are you French?", "type": "boolean"}],
        )
    )
    assert "Are you French?" in flat["all_questions"]
    assert "Is an SME" in flat["formatted_criteria"]
    assert "Objective: Strong fit" in flat["formatted_fits"]
    assert flat["formatted_fits"].count("\n") == 4  # 5 fit lines
