"""Turn a sanity-check ``decision`` into a Search Match row.

Faithful port of the n8n sub-workflow ``1.1 A`` tail:
* :func:`evaluate_gate`            -> the ``Decision`` node (pass/fail).
* :func:`flatten_decision`         -> ``Prepare fields4`` + ``Prepare fields5``
                                      (the canonical ``Raw Json`` shape).
* :func:`build_search_match_fields`-> ``Create an opportunity1`` (write-back),
                                      **with the one intended fix**: Budget Fit
                                      Status is sourced from the budget verdict,
                                      not the objective verdict.

Only PASS decisions become Search Match rows; FAIL ones are dropped (counted in
the funnel, never written), matching the n8n IF routing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# Exact Airtable singleSelect option sets (captured from the live base). The
# prompt emits a subset of these; anything outside the set is coerced to a safe
# fallback so we never 422 on create or silently create stray options.
_FIT_OPTIONS = {
    "Strong fit",
    "Potential fit",
    "Likely fit",
    "Unlikely fit",
    "Low fit",
    "No fit",
    "Unclear",
}
_ELIGIBILITY_OPTIONS = {
    "Eligible",
    "Likely Eligible",
    "Unclear",
    "Likely Ineligible",
    "Ineligible",
    "Not Eligible",
    "No Information",
}
# Search Match "Type" singleSelect options (captured from the live base). The
# sanity check classifies each PASS match into one of these; anything else is
# omitted rather than coerced, so a stray value never lands in Airtable.
_TYPE_OPTIONS = {
    "Quick Win",
    "Strategic Bid",
    "Stretch Fit",
}

# The four feasibility/fit dimensions the gate inspects.
_GATE_FIT_KEYS = (
    "objective_fit_status",
    "activity_fit_status",
    "budget_fit_status",
    "capacity_fit_status",
)


def _s(value: Any) -> str:
    """Decision values are meant to be strings; coerce defensively."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _arr(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _coerce(value: str, allowed: set[str], fallback: str = "Unclear") -> str:
    return value if value in allowed else fallback


# ---------------------------------------------------------------------------
# gate (port of the ``Decision`` node)
# ---------------------------------------------------------------------------
@dataclass
class GateResult:
    verdict: str  # "pass" | "fail"
    reasons: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"


def evaluate_gate(decision: dict[str, Any]) -> GateResult:
    """FAIL on Not Eligible, any Dealbreaker, any No-fit, or >1 Unlikely-fit."""
    reasons: list[str] = []

    if _s(decision.get("hard_requirement_match_verdict")) == "Not Eligible":
        reasons.append("Hard requirement verdict is Not Eligible")

    dealbreakers = [
        c
        for c in _arr(decision.get("eligibility_criteria"))
        if isinstance(c, dict) and c.get("status") == "Dealbreaker"
    ]
    if dealbreakers:
        joined = "; ".join(_s(d.get("criteria")) for d in dealbreakers)
        reasons.append(f"Eligibility dealbreaker(s): {joined}")

    fits = {key: _s(decision.get(key)) for key in _GATE_FIT_KEYS}
    no_fits = [name for name, status in fits.items() if status == "No fit"]
    if no_fits:
        reasons.append("No fit in: " + ", ".join(n.split("_")[0] for n in no_fits))

    unlikely = [name for name, status in fits.items() if status == "Unlikely fit"]
    if len(unlikely) > 1:
        reasons.append(
            f"{len(unlikely)} Unlikely fits: "
            + ", ".join(n.split("_")[0] for n in unlikely)
        )

    return GateResult(verdict="pass" if not reasons else "fail", reasons=reasons)


# ---------------------------------------------------------------------------
# flatten (port of ``Prepare fields4`` + ``Prepare fields5``)
# ---------------------------------------------------------------------------
_QUESTION_FIELDS = (
    "Clarification Questions",
    "Objective Questions",
    "Activities Questions",
    "Capacity Questions",
)
_CRITERIA_FIELDS = ("Eligibility Criteria", "Priority Criteria")
_FIT_FIELDS = (
    ("Eligibility", "Eligibility Status"),
    ("Objective", "Objectives Fit Level"),
    ("Activity", "Activity Fit Level"),
    ("Budget", "Budget Fit Level"),
    ("Capacity", "Capacity Fit Level"),
)


def _status_emoji(status: str) -> str:
    s = status.lower()
    if "pass" in s:
        return "✅"
    if "dealbreaker" in s or "fail" in s:
        return "❌"
    if "unclear" in s:
        return "❓"
    return "⚪"


def _fit_emoji(level: str) -> str:
    le = level.lower()
    if le in ("eligible", "likely eligible"):
        return "🟢"
    if le == "not eligible":
        return "🔴"
    if "strong fit" in le or "likely fit" in le:
        return "🟢"
    if "unlikely fit" in le:
        return "🟡"
    if "no fit" in le:
        return "🔴"
    return "⚪"


def flatten_decision(decision: dict[str, Any]) -> dict[str, Any]:
    """Build the canonical ``Raw Json`` object the matches endpoint expects."""
    flat: dict[str, Any] = {
        "Match Level": _s(decision.get("hard_requirement_match_verdict")),
        "Hook sentence": _s(decision.get("Hook")),
        "Match Summary": _s(decision.get("Match summary")),
        "Objectives Fit Level": _s(decision.get("objective_fit_status")),
        "Budget Fit Level": _s(decision.get("budget_fit_status")),
        "Eligibility Status": _s(decision.get("hard_requirement_match_verdict")),
        "Clarification Questions": _arr(decision.get("eligibility_clarification_questions")),
        "Eligibility Criteria": _arr(decision.get("eligibility_criteria")),
        "Activities summa summarum": _s(decision.get("summa_summarum_activity")),
        "Objectives summa summarum": _s(decision.get("summa_summarum_objective")),
        "Budget summa summarum": _s(decision.get("summa_summarum_budget")),
        "Consortium": decision.get("consortium") or {},
        "Priority Criteria": _arr(decision.get("priority_eligibility_criteria")),
        "Objective Questions": _arr(decision.get("objective_upgrade_question")),
        "Activities Questions": _arr(decision.get("activity_upgrade_question")),
        "Capacity Fit Level": _s(decision.get("capacity_fit_status")),
        "Activity Fit Level": _s(decision.get("activity_fit_status")),
        "Match Type": _s(decision.get("match_type")),
    }

    questions: list[str] = []
    for fname in _QUESTION_FIELDS:
        for entry in _arr(flat.get(fname)):
            if isinstance(entry, dict) and entry.get("question"):
                questions.append(str(entry["question"]))
            elif isinstance(entry, str):
                questions.append(entry)
    flat["all_questions"] = (
        "\n".join(f"- {q}" for q in questions) if questions else "No questions found."
    )

    criteria: list[str] = []
    for fname in _CRITERIA_FIELDS:
        for entry in _arr(flat.get(fname)):
            if isinstance(entry, dict) and entry.get("criteria"):
                status = entry.get("status") or "Unknown"
                criteria.append(f"{_status_emoji(str(status))} {status}: {entry['criteria']}")
    flat["formatted_criteria"] = "\n".join(criteria) if criteria else "No criteria found."

    fits: list[str] = []
    for label, fname in _FIT_FIELDS:
        level = flat.get(fname)
        if level:
            fits.append(f"{_fit_emoji(str(level))} {label}: {level}")
    flat["formatted_fits"] = "\n".join(fits) if fits else "No fit levels found."

    return flat


# ---------------------------------------------------------------------------
# write-back (port of ``Create an opportunity1`` + the Budget Fit Status fix)
# ---------------------------------------------------------------------------
def build_search_match_fields(
    decision: dict[str, Any],
    *,
    grant_id: str,
    company_id: str,
    api_job_id: str = "",
    notification_status: str = "Pending",
) -> dict[str, Any]:
    """Map a PASS decision to Search Match columns (Airtable field names)."""
    flat = flatten_decision(decision)
    consortium = flat.get("Consortium") or {}
    missing_roles = consortium.get("missing_partner_roles") if isinstance(consortium, dict) else None

    fields: dict[str, Any] = {
        "Grant": [grant_id],
        "Company": [company_id],
        "Match Description": f"{flat['Hook sentence']}\n\n{flat['Match Summary']}",
        "Raw Json": json.dumps(flat, ensure_ascii=False),
        "Discovery Questions": flat["all_questions"],
        "Eligibility Status": _coerce(flat["Eligibility Status"], _ELIGIBILITY_OPTIONS),
        "Objective Fit": flat["Objectives summa summarum"],
        # FIX: the n8n wrote Objectives Fit Level here; the real budget verdict
        # belongs in the budget column.
        "Budget Fit Status": _coerce(flat["Budget Fit Level"], _FIT_OPTIONS),
        "Possible Workplan Angles": flat["Activities summa summarum"],
        "Funding Gap": flat["Budget summa summarum"],
        "Missing Partner Roles": json.dumps(missing_roles or [], ensure_ascii=False),
        "Objective Fit Status": _coerce(flat["Objectives Fit Level"], _FIT_OPTIONS),
        "Activity Fit Status": _coerce(flat["Activity Fit Level"], _FIT_OPTIONS),
        "Eligibility Fit": flat["formatted_criteria"],
        "api_job_id": api_job_id,
        "Capacity Fit": _coerce(flat["Capacity Fit Level"], _FIT_OPTIONS),
        "Notification Status": notification_status,
    }
    # Only write Type when the model emitted a valid option; never coerce to a
    # stray value (legacy/odd rows simply keep an empty Type).
    if flat["Match Type"] in _TYPE_OPTIONS:
        fields["Type"] = flat["Match Type"]
    return fields
