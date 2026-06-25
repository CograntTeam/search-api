"""Company-classification prompt builder + the lenient classification model."""

from __future__ import annotations

from app.models.company_classification import CompanyClassification
from app.services.classification_prompt import build_classification_prompt


def test_prompt_injects_description_and_today():
    p = build_classification_prompt(
        company_description="ACME makes industrial robots.", today="2026-06-25"
    )
    assert "ACME makes industrial robots." in p
    assert "2026-06-25" in p
    # Tokens fully substituted.
    assert "__COMPANY_DESCRIPTION__" not in p
    assert "__TODAY__" not in p
    # Verbatim instruction markers survive.
    assert "ROLE: You are an expert in classifying companies" in p
    assert "ANSWER FORMAT" in p
    assert "SUBJECT-MATTER EXPERTISE" in p


def test_to_company_fields_maps_and_applies_defaults():
    c = CompanyClassification.model_validate(
        {
            "eligibility_basics": {
                "company_size_class": "Small",
                "date_of_establishment": "2015-01-01",
            },
            "activity_sectors": {"values": ["Software Product Development"]},
            "verticality": {
                "is_horizontal": False,
                "vertical_sectors_impacted": ["Pharmaceuticals and Life Sciences"],
            },
            "beneficiary_archetype": {"values": ["Innovative SME", "Generic SME"]},
            "subject_expertise": {"domains": ["AI/ML foundational research"]},
        }
    )
    f = c.to_company_fields()
    assert f["Company Size Class"] == "Small"
    assert f["Activity Sectors"] == ["Software Product Development"]
    assert f["Application Area"] == ["Pharmaceuticals and Life Sciences"]
    assert f["Organisation Archetype"] == "Innovative SME"  # first value only
    assert f["Subject Expertise"] == ["AI/ML foundational research"]
    assert f["Date of Organisation Establishment"] == "2015-01-01"
    # Hardcoded, per the n8n Save Topic Area node.
    assert f["Consortium Stance"] == "Consortium Optional"
    assert "Grant - Lump Sum" in f["Acceptable Instruments"]
    assert f["Search Lite"] is False


def test_year_only_date_becomes_jan_first():
    # "Founded 2018" → the model often emits a bare year; a date field needs ISO.
    c = CompanyClassification.model_validate(
        {"eligibility_basics": {"date_of_establishment": "2018"}}
    )
    assert c.to_company_fields()["Date of Organisation Establishment"] == "2018-01-01"


def test_unparseable_date_is_dropped_not_written():
    # A non-date value must never be written (it would 422 the date field).
    c = CompanyClassification.model_validate(
        {"eligibility_basics": {"date_of_establishment": "circa 2018, unclear"}}
    )
    assert "Date of Organisation Establishment" not in c.to_company_fields()


def test_full_iso_date_is_kept():
    c = CompanyClassification.model_validate(
        {"eligibility_basics": {"date_of_establishment": "2015-03-09T00:00:00.000Z"}}
    )
    assert c.to_company_fields()["Date of Organisation Establishment"] == "2015-03-09"


def test_application_area_falls_back_to_horizontal():
    c = CompanyClassification.model_validate(
        {"verticality": {"is_horizontal": True, "vertical_sectors_impacted": []}}
    )
    assert c.to_company_fields()["Application Area"] == ["Horizontal"]


def test_lenient_on_empty_payload():
    c = CompanyClassification.model_validate({})
    f = c.to_company_fields()
    # Missing scalars dropped (never blank an existing cell)...
    assert "Company Size Class" not in f
    assert "Organisation Archetype" not in f
    # ...but the hardcoded defaults are always written.
    assert f["Consortium Stance"] == "Consortium Optional"
    assert f["Application Area"] == ["Horizontal"]


def test_markdown_profile_has_sections():
    c = CompanyClassification.model_validate(
        {"activity_sectors": {"values": ["Software Product Development"]}}
    )
    md = c.to_markdown()
    assert "# Company Classification Profile" in md
    assert "Activity Sectors" in md
    assert "Software Product Development" in md
