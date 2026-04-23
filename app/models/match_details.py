"""Pydantic models describing the ``match`` block returned by
``GET /v1/searches/{job_id}/matches``.

Match payload keys in production are Title Case strings (``Short Grant
Title``, ``Match Level``, ``Budget Fit Level``, …). Python attribute
names can't hold spaces, so every field here uses a Pydantic alias
pointing at the on-the-wire key. ``populate_by_name=True`` lets partners
construct the model from either form, and every route that returns a
match sets ``response_model_by_alias=True`` so the wire shape stays
exactly as n8n writes it today.

As with :mod:`grant_details`, every field is ``Optional`` and every
model allows extras — the match block is AI-produced and may grow over
time; we never want a new key to break serialisation.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


_CFG = ConfigDict(extra="allow", populate_by_name=True)


class EligibilityCriterion(BaseModel):
    """One row in ``Eligibility Criteria`` / ``Priority Criteria``."""

    model_config = _CFG

    criteria: str | None = Field(
        None,
        description="The criterion in up to 10 words.",
    )
    status: str | None = Field(
        None,
        description="``Pass`` / ``Fail`` / ``Unclear`` / ``Dealbreaker``.",
    )


class ClarificationQuestion(BaseModel):
    """One yes/no question surfaced to the company.

    Appears under ``Clarification Questions``, ``Objective Questions``,
    and ``Activities Questions``. Answering resolves an ``Unclear`` item
    to ``Pass`` / ``Fail`` / ``Dealbreaker`` (for eligibility) or
    upgrades a fit level (for objective / activity).
    """

    model_config = _CFG

    question: str | None = Field(
        None,
        description="Short plain-language question, answerable yes/no.",
    )
    type: str | None = Field(
        None, description="Always ``boolean`` today."
    )


class Consortium(BaseModel):
    """Consortium expectations for this match."""

    model_config = _CFG

    required: str | None = Field(
        None,
        description=(
            "``Required`` / ``Optional`` / ``Single Applicant`` — whether "
            "a consortium is mandatory, optional, or disallowed."
        ),
    )
    potential_role: str | None = Field(
        None,
        description=(
            "``Coordinator`` or ``Partner``. Present only when a "
            "consortium is allowed."
        ),
    )
    missing_partner_roles: list[str] | None = Field(
        None,
        description=(
            "Roles required to make the project credible/eligible that "
            "the company likely needs to recruit (pilot site, certified "
            "lab, public authority, manufacturing line, university, "
            "etc.). Informational only — does NOT influence any verdict."
        ),
    )


class MatchDetails(BaseModel):
    """Analyst-style decision block attached to every Search Match.

    Produced by the Grant–Company Match Sanity Checker agent. The shape
    is stable enough to expose in the public schema, but individual
    field values are free-form strings.

    Every field has both a Python-safe attribute name and the Title Case
    alias that appears on the wire. Partners should read the Title Case
    keys from JSON; the Python names only matter inside the gateway.
    """

    model_config = _CFG

    # ── Summary header ──────────────────────────────────────────────
    short_grant_title: str | None = Field(
        None,
        alias="Short Grant Title",
        description=(
            "Reader-friendly title with heavy codes stripped, keeping "
            "the core distinguishing keywords."
        ),
    )
    hook_sentence: str | None = Field(
        None,
        alias="Hook sentence",
        description=(
            "Short commercial tagline teasing what the grant achieves "
            "(verb + change-the-world style)."
        ),
    )
    match_summary: str | None = Field(
        None,
        alias="Match Summary",
        description=(
            "2–3 sentences explaining why the company should or should "
            "not apply. Decision-informing analysis only — does not "
            "retell the company's own story."
        ),
    )
    match_level: str | None = Field(
        None,
        alias="Match Level",
        description=(
            "Overall fit verdict summarising eligibility + objective + "
            "activity signals."
        ),
    )

    # ── Hard eligibility ────────────────────────────────────────────
    eligibility_status: str | None = Field(
        None,
        alias="Eligibility Status",
        description=(
            "Hard requirement match verdict — ``Eligible``, ``Likely "
            "Eligible``, ``Unclear``, ``Not Eligible``, or ``No "
            "Information``."
        ),
    )
    eligibility_criteria: list[EligibilityCriterion] | None = Field(
        None,
        alias="Eligibility Criteria",
        description="Every explicitly stated eligibility rule, each with a status.",
    )
    priority_criteria: list[EligibilityCriterion] | None = Field(
        None,
        alias="Priority Criteria",
        description=(
            "Explicitly stated priority (bonus-point) eligibility "
            "criteria, each with a status."
        ),
    )
    clarification_questions: list[ClarificationQuestion] | None = Field(
        None,
        alias="Clarification Questions",
        description=(
            "Yes/no questions used to resolve gaps on Hard Eligibility. "
            "Answering them reassigns ``Unclear`` items to ``Pass``, "
            "``Fail``, or ``Dealbreaker``."
        ),
    )
    formatted_criteria: str | None = Field(
        None,
        description="Pre-rendered bullet list of all eligibility criteria + status.",
    )
    all_questions: str | None = Field(
        None,
        description=(
            "All open clarification / upgrade questions flattened to a "
            "single string for convenience."
        ),
    )

    # ── Objective fit ───────────────────────────────────────────────
    objectives_fit_level: str | None = Field(
        None,
        alias="Objectives Fit Level",
        description="``Strong fit`` / ``Potential fit`` / ``No fit``.",
    )
    objectives_summa_summarum: str | None = Field(
        None,
        alias="Objectives summa summarum",
        description=(
            "One sentence summarising thematic alignment between the "
            "company's objective and the call's intent."
        ),
    )
    objective_questions: list[ClarificationQuestion] | None = Field(
        None,
        alias="Objective Questions",
        description=(
            "A single high-stakes question designed to reframe "
            "``Potential fit`` into ``Strong fit`` at the objective "
            "level. Empty for ``Strong fit`` or ``No fit``."
        ),
    )

    # ── Activity fit ────────────────────────────────────────────────
    activity_fit_level: str | None = Field(
        None,
        alias="Activity Fit Level",
        description="``Strong fit`` / ``Potential fit`` / ``No fit``.",
    )
    activities_summa_summarum: str | None = Field(
        None,
        alias="Activities summa summarum",
        description=(
            "One sentence summarising how well the work the company "
            "performs matches the work the call finances."
        ),
    )
    activities_questions: list[ClarificationQuestion] | None = Field(
        None,
        alias="Activities Questions",
        description=(
            "A single high-stakes question designed to reframe "
            "``Potential fit`` into ``Strong fit`` at the activity level."
        ),
    )

    # ── Budget fit ──────────────────────────────────────────────────
    budget_fit_level: str | None = Field(
        None,
        alias="Budget Fit Level",
        description=(
            "``Strong fit`` / ``Potential fit`` / ``No fit`` — whether "
            "the funding range matches the company's funding goals."
        ),
    )
    budget_summa_summarum: str | None = Field(
        None,
        alias="Budget summa summarum",
        description=(
            "Plain-words comparison of the call's funding range and the "
            "company's funding goals."
        ),
    )

    # ── Consortium ──────────────────────────────────────────────────
    consortium: Consortium | None = Field(
        None,
        alias="Consortium",
        description="Consortium expectations, potential role, and missing roles.",
    )

    # Documented escape hatch mirrors the one on GrantDetails.
    def model_post_init(self, __context: Any) -> None:  # noqa: D401
        """Extras are allowed and pass through unchanged."""
