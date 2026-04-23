"""Pydantic models describing the ``grant`` block returned by
``GET /v1/searches/{job_id}/matches``.

These models exist purely so OpenAPI / Swagger shows every field that
partners can expect to see. Every field is ``Optional`` and every model
sets ``extra="allow"`` — the underlying JSON is produced by AI agents
upstream and may evolve; we never want a new field to break a response.

Sources of truth for field descriptions are the AI-agent prompts that
populate these structures (kept verbatim where reasonable so there's one
canonical description).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# A shared config so every grant-details sub-model allows extra fields
# and round-trips cleanly when constructed from loose dicts.
_CFG = ConfigDict(extra="allow", populate_by_name=True)


class CoreMetadata(BaseModel):
    model_config = _CFG

    managing_authority: str | None = Field(
        None, description="Name of the institution managing or administering the grant."
    )
    funding_provider: str | None = Field(
        None,
        description=(
            "Ultimate source of the funds (e.g. European Union, "
            "NextGenerationEU, a national ministry)."
        ),
    )
    program_name: str | None = Field(
        None, description="Official title of the overarching programme the grant belongs to."
    )
    grant_name: str | None = Field(
        None, description="Official title of this specific funding call, in English."
    )
    grant_name_native: str | None = Field(
        None, description="Official grant name in the native language of the funding call."
    )
    short_grant_name: str | None = Field(
        None,
        description=(
            "Simplified, short name for the grant in plain language — "
            "heavy technical jargon stripped. English."
        ),
    )
    reference_id: str | None = Field(
        None, description="Official call ID or reference number; ``null`` if none."
    )


class ProjectDuration(BaseModel):
    model_config = _CFG

    duration_type: str | None = Field(
        None,
        description="One of ``Years`` / ``Months`` / ``Days``.",
    )
    minimum_duration: int | None = Field(
        None, description="Minimum allowed full project duration; ``null`` if none."
    )
    maximum_duration: int | None = Field(
        None, description="Maximum allowed full project duration; ``null`` if none."
    )


class ApplicationStage(BaseModel):
    model_config = _CFG

    stage_name: str | None = Field(
        None,
        description=(
            "Name of the stage or batch (e.g. ``Concept Note``, "
            "``Full Proposal``, ``Batch 1``). Only forward-looking stages "
            "are included — past batches are omitted."
        ),
    )
    deadline_date: str | None = Field(
        None,
        description=(
            "Deadline for this stage in ``YYYY-MM-DD``. Empty when no hard "
            "deadline is approaching for a continuous call."
        ),
    )
    deadline_conditions: str | None = Field(
        None,
        description=(
            "Conditions attached to this deadline (e.g. ``Only for "
            "applicants who passed stage 1``)."
        ),
    )


class Timelines(BaseModel):
    model_config = _CFG

    application_opening_date: str | None = Field(
        None, description="When the call officially opens for submissions (``YYYY-MM-DD``)."
    )
    is_continuous_call: bool | None = Field(
        None,
        description=(
            "``true`` if the call is open continuously until funds run out; "
            "``false`` if it has a hard deadline."
        ),
    )
    application_process_type: str | None = Field(
        None,
        description=(
            "``Single-stage`` / ``Two-stage`` / ``Continuous with cut-offs`` / "
            "``Rolling evaluation``."
        ),
    )
    early_closure_possible: bool | None = Field(
        None,
        description=(
            "``true`` if the call can close before the deadline (e.g. after "
            "a cap on applications or funds exhausted)."
        ),
    )
    early_closure_conditions: str | None = Field(
        None,
        description="Exact conditions for early closure.",
    )
    application_stages: list[ApplicationStage] | None = Field(
        None, description="Forward-looking stages/batches with their deadlines."
    )
    evaluation_process: list[str] | None = Field(
        None,
        description=(
            "Steps of the evaluation process — what is assessed at each "
            "step plus the expected timeline from submission to result."
        ),
    )
    project_duration: ProjectDuration | None = Field(
        None, description="Allowed project length bounds."
    )
    project_start_date_rules: str | None = Field(
        None,
        description=(
            "When the project must start (e.g. immediately after contract "
            "signature, a specific date, within 3 months of award)."
        ),
    )
    project_timeline_conditions: str | None = Field(
        None,
        description="Other conditions related to the implementation timeline.",
    )


class FundingTier(BaseModel):
    model_config = _CFG

    tier_condition: str | None = Field(
        None,
        description="Condition for this funding bracket (e.g. ``SMEs``, ``Standard``).",
    )
    minimum_funding_ticket: int | None = Field(
        None, description="Minimum grant amount for this tier."
    )
    maximum_funding_ticket: int | None = Field(
        None, description="Maximum grant amount for this tier."
    )
    max_funding_intensity_pct: int | None = Field(
        None, description="Max percentage of eligible costs covered for this tier."
    )


class Financials(BaseModel):
    model_config = _CFG

    currency: str | None = Field(
        None, description="Currency the grant is awarded in (``EUR``, ``USD``, …)."
    )
    overall_total_call_budget: int | None = Field(
        None,
        description="Total budget for the entire funding call across all projects.",
    )
    number_of_projects_awarded: int | None = Field(
        None, description="How many applicants will be awarded."
    )
    pre_financing_percentage: int | None = Field(
        None,
        description="Percentage of the grant given upfront as an advance payment.",
    )
    funding_tiers: list[FundingTier] | None = Field(
        None, description="Per-condition funding brackets."
    )
    support_forms_picklist: list[str] | None = Field(
        None,
        description=(
            "All applicable forms of support — e.g. ``Grant - Lump Sum``, "
            "``Grant - Actual Costs``, ``Grant - De Minimis``, "
            "``Blended Finance``, ``Equity Investment``, ``Loan``, "
            "``Voucher``, ``Prize``, ``Scholarship/Fellowship``, "
            "``Tax Credit``, ``Subsidy``, ``In-kind Support``."
        ),
    )
    support_form_conditions: list[str] | None = Field(
        None,
        description=(
            "Conditions for support forms including co-financing and own-"
            "contribution rules, stacking rules with other grants, "
            "minimum cash thresholds."
        ),
    )
    eligible_costs: list[str] | None = Field(
        None,
        description=(
            "Line items that can be funded. Nuances / caps / choices in "
            "parentheses (e.g. ``Overheads capped at 15%``)."
        ),
    )
    non_eligible_costs: list[str] | None = Field(
        None, description="Explicitly named costs that are NOT funded."
    )
    funds_disbursement_timeline: str | None = Field(
        None,
        description=(
            "When and how funds are given to the company (e.g. reimbursed "
            "quarterly based on milestones) and what conditions trigger "
            "disbursement."
        ),
    )


class EligibilityAndConsortia(BaseModel):
    model_config = _CFG

    geography_level: str | None = Field(
        None,
        description="``European`` / ``Regional`` / ``National`` / ``Local``.",
    )
    eligible_countries_organisation: list[str] | None = Field(
        None,
        description=(
            "Countries where the applicant organisation must be registered "
            "(only populated for Regional/National)."
        ),
    )
    eligible_countries_activities: list[str] | None = Field(
        None,
        description="Countries where project activities must be carried out.",
    )
    eligible_local_regions_organisation: list[str] | None = Field(
        None,
        description="Specific regions/municipalities of registration for Local calls.",
    )
    eligible_local_regions_activities: list[str] | None = Field(
        None,
        description="Specific regions/municipalities for project activities, Local calls.",
    )
    geographical_conditions_description: str | None = Field(
        None,
        description=(
            "Prose covering nuances: where the project must be executed vs. "
            "where the applicant is registered vs. where beneficiaries are."
        ),
    )
    eligible_legal_forms_picklist: list[str] | None = Field(
        None,
        description=(
            "All that apply — e.g. ``Private Business``, ``Non-profit``, "
            "``Research Institution``, ``Public Institution``, "
            "``Natural Person``."
        ),
    )
    min_trl: int | None = Field(
        None,
        description=(
            "Minimum Technology Readiness Level required to be already "
            "achieved at the time of application (1–9)."
        ),
    )
    max_trl: int | None = Field(
        None,
        description=(
            "Maximum Technology Readiness Level already achieved at the "
            "time of application (1–9)."
        ),
    )
    eligibility_conditions: list[str] | None = Field(
        None,
        description=(
            "Constraints on organisations applying (age, sector, "
            "certifications, infrastructure, etc.)."
        ),
    )
    consortium_setup: str | None = Field(
        None,
        description=(
            "``Mono-beneficiary Only`` / ``Consortium Required`` / "
            "``Consortium Optional``."
        ),
    )
    min_consortium_size: int | None = Field(
        None,
        description="Minimum number of partners required (``1`` if mono-beneficiary allowed).",
    )
    min_countries_in_consortium: int | None = Field(
        None, description="Minimum number of different eligible countries in the consortium."
    )
    required_consortium_roles: list[str] | None = Field(
        None, description="Specific roles or entity types required in the consortium."
    )
    exclusion_criteria: list[str] | None = Field(
        None,
        description=(
            "Explicit conditions / entity types that CANNOT apply "
            "(e.g. ``Undertakings in difficulty``, ``Tobacco sector``)."
        ),
    )
    explicit_org_priority_criteria: list[str] | None = Field(
        None,
        description=(
            "Characteristics the call EXPLICITLY rewards with bonus "
            "points or priority status. Weights appear in parentheses."
        ),
    )
    implicit_org_priority_criteria: list[str] | None = Field(
        None,
        description=(
            "Profiles the call clearly favours through its language or "
            "design even without formal bonus points. Each entry cites "
            "the specific signal from the call that supports the inference."
        ),
    )


class ScopeAndActivities(BaseModel):
    model_config = _CFG

    funding_call_goal: str | None = Field(
        None, description="One-sentence statement of the call's overarching impact."
    )
    problem_solved_summary: str | None = Field(
        None,
        description=(
            "2–4 sentence plain-language summary of the concrete problem "
            "this call exists to solve. ``Not explicitly stated in "
            "source`` when the call does not describe it."
        ),
    )
    related_policies_and_acts: list[str] | None = Field(
        None,
        description=(
            "Specific policies, laws, acts, or strategic documents this "
            "call implements (e.g. EU Green Deal, AI Act)."
        ),
    )
    project_nature_typology: list[str] | None = Field(
        None,
        description=(
            "Core natures — ``R&D``, ``Infrastructure/CapEx``, "
            "``Training/Upskilling``, ``Commercialization/Go-to-Market``, "
            "``Event/Networking``, ``Policy/Advocacy``, "
            "``Sustainability Transition``."
        ),
    )
    technology_focus_typology: list[str] | None = Field(
        None,
        description=(
            "Technological / thematic focuses — ``Digital/ICT``, "
            "``DeepTech/AI``, ``GreenTech/CleanTech``, ``Health/MedTech``, "
            "``BioTech``, ``AgTech``, ``Social Innovation``, "
            "``Creative/Cultural``, ``Sector-Agnostic``."
        ),
    )
    primary_beneficiary_typology: list[str] | None = Field(
        None,
        description=(
            "Who ultimately benefits — ``Startups``, ``Scaleups/SMEs``, "
            "``Academia/Researchers``, ``Public Sector``, ``General "
            "Citizens``, ``Marginalized Groups``."
        ),
    )
    supported_activities: list[str] | None = Field(
        None, description="Eligible activity types. One bullet = one activity."
    )
    expected_concrete_deliverables: list[str] | None = Field(
        None,
        description=(
            "Tangible outputs, near-term outcomes, and systemic impacts "
            "the project is expected to produce."
        ),
    )
    project_evaluation_priority_criteria: list[str] | None = Field(
        None,
        description=(
            "Criteria used to score the application (e.g. ``[IMPACT]`` "
            "prefix, weights/max points in parentheses)."
        ),
    )


class ScopeBatch(BaseModel):
    model_config = _CFG

    batch_name: str | None = Field(
        None,
        description=(
            "Name of the thematic track / challenge / lot / topic / sub-call. "
            "Only present when the call explicitly defines separate tracks."
        ),
    )
    batch_description: str | None = Field(
        None, description="One-sentence description of what this batch funds."
    )
    batch_specific_eligibility: list[str] | None = Field(
        None,
        description=(
            "ONLY eligibility conditions that diverge from the main "
            "eligibility_and_consortia fields."
        ),
    )
    batch_specific_deliverables: list[str] | None = Field(
        None,
        description=(
            "ONLY deliverables that diverge from ``expected_concrete_deliverables``."
        ),
    )


class Administrative(BaseModel):
    model_config = _CFG

    application_language: list[str] | None = Field(
        None, description="Accepted languages (e.g. ``[\"English\"]``)."
    )
    submission_process_description: str | None = Field(
        None,
        description=(
            "Broad description of the submission process (pitch to jury, "
            "physical mailing, etc.)."
        ),
    )
    submission_portal_name: str | None = Field(
        None, description="Name of the portal/IT system."
    )
    submission_portal_url: str | None = Field(
        None, description="URL of the submission portal."
    )
    required_submission_documents: list[str] | None = Field(
        None,
        description=(
            "Documents the applicant must attach at submission — business "
            "plan, letters of intent, ethics self-assessments, signed "
            "declarations, audited financials, CVs, etc. Format/page "
            "limits in parentheses where stated."
        ),
    )
    post_award_administration_process: str | None = Field(
        None,
        description=(
            "Project administration after winning — reporting frequency, "
            "milestones, payment triggers."
        ),
    )
    official_contact_email: str | None = Field(
        None, description="Primary email for applicant support / Q&A."
    )
    audit_and_reporting_burden: str | None = Field(
        None,
        description=(
            "Admin heaviness — auditor certificates, timesheets, etc."
        ),
    )


class GrantDetails(BaseModel):
    """Structured grant metadata attached to every Search Match.

    Every section is produced by a dedicated upstream agent; fields
    marked ``Optional`` reflect that absence of data is common and we
    never want partners to parse defensively around nulls they already
    handle elsewhere.
    """

    model_config = _CFG

    core_metadata: CoreMetadata | None = None
    timelines: Timelines | None = None
    financials: Financials | None = None
    eligibility_and_consortia: EligibilityAndConsortia | None = None
    scope_and_activities: ScopeAndActivities | None = None
    scope_batches: list[ScopeBatch] | None = Field(
        None,
        description=(
            "Separate thematic tracks if the call defines them, otherwise "
            "an empty array."
        ),
    )
    administrative: Administrative | None = None

    # Catch-all: when upstream adds a new top-level section we don't
    # know about yet, it still round-trips through the response.
    def model_post_init(self, __context: Any) -> None:  # noqa: D401
        """Documented so downstream readers know about the escape hatch."""
