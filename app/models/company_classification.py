"""Company classification produced by the forward-search Gemini step.

Mirrors the JSON the n8n *AI Agent3* node emits (the verbatim prompt lives in
``app/services/classification_prompt.py``). Lenient by design — the payload is
AI-generated, so ``extra="allow"`` and every field defaults, so a partial or
malformed object degrades gracefully rather than raising.

:meth:`CompanyClassification.to_company_fields` ports the n8n *Save Topic Area*
write-back; :meth:`to_markdown` ports *Transform to markdown* (appended to the
company's description).
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_CFG = ConfigDict(extra="allow", populate_by_name=True)


def _normalize_iso_date(value: Any) -> str | None:
    """Coerce the LLM's ``date_of_establishment`` to an Airtable-writable ISO date,
    or ``None`` when it can't be parsed.

    ``Date of Organisation Establishment`` is a date field, so a non-date string
    (e.g. ``"2018"`` from "Founded 2018", or a sentence) would 422 the write —
    and the prompt's JSON example shows ``"date_of_establishment": date``, so the
    model does emit loose values. Full ISO is kept; a bare 4-digit year becomes
    Jan 1; anything else is dropped (the field is optional)."""
    if not isinstance(value, str):
        return None
    v = value.strip()
    if re.fullmatch(r"\d{4}", v):
        return f"{v}-01-01"
    m = re.match(r"\d{4}-\d{2}-\d{2}", v)
    if m:
        try:
            date.fromisoformat(m.group(0))
        except ValueError:
            return None
        return m.group(0)
    return None

# Hardcoded at classification time, exactly as the n8n *Save Topic Area* node does
# (the classifier does not decide these).
_DEFAULT_CONSORTIUM_STANCE = "Consortium Optional"
_DEFAULT_ACCEPTABLE_INSTRUMENTS = [
    "Grant - Lump Sum",
    "Grant - Actual Costs",
    "Grant - State Aid",
    "Grant - De Minimis",
]


class EligibilityBasics(BaseModel):
    model_config = _CFG
    date_of_establishment: str | None = None
    years_established: int | None = None
    company_size_class: str | None = None
    reasoning: str | None = None


class ActivitySectors(BaseModel):
    model_config = _CFG
    values: list[str] = Field(default_factory=list)
    reasoning: str | None = None


class Verticality(BaseModel):
    model_config = _CFG
    is_horizontal: bool | None = None
    vertical_sectors_impacted: list[str] = Field(default_factory=list)
    reasoning: str | None = None


class BeneficiaryArchetype(BaseModel):
    model_config = _CFG
    values: list[str] = Field(default_factory=list)
    reasoning: str | None = None


class SubjectExpertise(BaseModel):
    model_config = _CFG
    domains: list[str] = Field(default_factory=list)
    specialisations: list[str] = Field(default_factory=list)
    reasoning: str | None = None


class CompanyClassification(BaseModel):
    model_config = _CFG
    eligibility_basics: EligibilityBasics = Field(default_factory=EligibilityBasics)
    activity_sectors: ActivitySectors = Field(default_factory=ActivitySectors)
    verticality: Verticality = Field(default_factory=Verticality)
    beneficiary_archetype: BeneficiaryArchetype = Field(default_factory=BeneficiaryArchetype)
    subject_expertise: SubjectExpertise = Field(default_factory=SubjectExpertise)

    def to_company_fields(self) -> dict[str, Any]:
        """Classification → Companies fields (ports n8n *Save Topic Area*).

        ``None``/empty scalars are dropped so we never blank an existing cell with
        a field the model omitted. ``Application Area`` falls back to
        ``["Horizontal"]`` when no vertical was identified, exactly as the n8n node.
        """
        eb = self.eligibility_basics
        archetype = (
            self.beneficiary_archetype.values[0]
            if self.beneficiary_archetype.values
            else None
        )
        fields: dict[str, Any] = {
            "Company Size Class": eb.company_size_class,
            "Activity Sectors": self.activity_sectors.values,
            "Application Area": self.verticality.vertical_sectors_impacted or ["Horizontal"],
            "Subject Expertise": self.subject_expertise.domains,
            "Organisation Archetype": archetype,
            "Date of Organisation Establishment": _normalize_iso_date(eb.date_of_establishment),
            # Hardcoded, per the n8n write-back.
            "Consortium Stance": _DEFAULT_CONSORTIUM_STANCE,
            "Acceptable Instruments": _DEFAULT_ACCEPTABLE_INSTRUMENTS,
            "Search Lite": False,
        }
        return {k: v for k, v in fields.items() if v not in (None, "")}

    def to_markdown(self) -> str:
        """Human-readable profile appended to the company description (ports the
        n8n *Transform to markdown* node)."""
        today = datetime.now(UTC).date().isoformat()

        def _list(items: list[str]) -> str:
            return "\n".join(f"- {i}" for i in items) if items else "_None listed_"

        def _val(v: Any) -> str:
            return str(v) if v not in (None, "") else "_Not stated_"

        eb = self.eligibility_basics
        vt = self.verticality
        se = self.subject_expertise
        lines: list[str] = [
            "# Company Classification Profile",
            "",
            f"_Generated {today}_",
            "",
            "## Eligibility Basics",
            "",
            f"- **Date of establishment:** {_val(eb.date_of_establishment)}",
            f"- **Years in operation:** {_val(eb.years_established)}",
            f"- **Size class:** {_val(eb.company_size_class)}",
            "",
            "---",
            "",
            "## Activity Sectors (capabilities)",
            "",
            _list(self.activity_sectors.values),
            "",
            "---",
            "",
            "## Verticality (sectors served)",
            "",
        ]
        if vt.is_horizontal is True:
            lines.append("**Sector-agnostic horizontal** — no industry concentration")
        elif vt.vertical_sectors_impacted:
            lines.append("**Vertical specialist** — concentrated in:")
            lines.append("")
            lines.append(_list(vt.vertical_sectors_impacted))
        else:
            lines.append("_Insufficient signals to classify_")
        lines += [
            "",
            "---",
            "",
            "## Beneficiary Archetype",
            "",
            _list(self.beneficiary_archetype.values),
            "",
            "---",
            "",
            "## Subject-Matter Expertise",
            "",
        ]
        if se.domains:
            lines.append("**Domains:**")
            lines.append("")
            lines.append(_list(se.domains))
            if se.specialisations:
                lines += ["", "**Specialisations:**", "", _list(se.specialisations)]
        else:
            lines.append(
                "_No demonstrated deep subject-matter expertise — expertise-gated "
                "grants will not match._"
            )
        return "\n".join(lines)
