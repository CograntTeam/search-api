"""Search-request payload models.

The public ``POST /v1/searches`` endpoint accepts two alternative payload
shapes:

* **Existing company** — partner already has a company profile stored in
  Cogrant's Companies table; they pass its record ID.
* **New company** — partner supplies the minimum data needed to create
  the company on the fly (name + description + country, optional website).
  The gateway creates the row first, then runs the search against the
  fresh record ID.

We validate both variants as a Pydantic discriminated-like union via a
model validator so OpenAPI docs stay clean and partners get precise 422
errors (``company_id must be provided OR company_name + description + country``).
``Organisation Type`` is **always** set to ``Private Business`` for
partner-created rows; if they need other entity types they talk to
Cogrant directly.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# What the gateway writes into Companies['Organisation type'] when it
# creates a row on behalf of a partner. Anything else is out of scope for
# the self-serve API.
DEFAULT_ORGANISATION_TYPE = "Private Business"


class NewCompanyFields(BaseModel):
    """The subset of Companies fields a partner may set when creating a
    company via the search API."""

    company_name: str = Field(
        ..., min_length=1, max_length=200,
        description="Legal or commonly-used company name."
    )
    company_description: str = Field(
        ..., min_length=1, max_length=5_000,
        description=(
            "Free-text description of what the company does. Feeds directly "
            "into the matching engine, so be specific about products, "
            "technology, and target market."
        ),
    )
    country: str = Field(
        ..., min_length=1, max_length=60,
        description=(
            "Country of incorporation. Must match one of the options on the "
            "Companies → Country field (e.g. ``Lithuania``, ``Germany``)."
        ),
    )
    website: str | None = Field(
        default=None,
        max_length=400,
        description="Optional. Full URL including scheme (``https://``).",
    )

    @field_validator("company_name", "company_description", "country")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class SearchPayload(BaseModel):
    """Inner ``payload`` of a ``POST /v1/searches`` request.

    Exactly one of:

    * ``company_id`` — existing company in Cogrant's Airtable base.
    * (``company_name`` + ``company_description`` + ``country``,
      optional ``website``) — new company; the gateway creates it first.

    Extra keys are ignored; this keeps forward compatibility open for
    fields we may add without breaking old clients.
    """

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {"company_id": "recABCDEFGHIJKLMN"},
                {
                    "company_name": "Acme Bio",
                    "company_description": "Produces fermentation-based "
                                           "protein for the food industry.",
                    "country": "Lithuania",
                    "website": "https://acme.bio",
                },
            ]
        },
    )

    company_id: str | None = Field(
        default=None,
        description="Airtable record ID of an existing Companies row.",
    )
    company_name: str | None = None
    company_description: str | None = None
    country: str | None = None
    website: str | None = None

    @model_validator(mode="after")
    def _exactly_one_branch(self) -> "SearchPayload":
        if self.company_id:
            # Existing-company branch. None of the new-company fields
            # should be set — if they are, it's ambiguous; reject.
            extra = [
                name for name, val in (
                    ("company_name", self.company_name),
                    ("company_description", self.company_description),
                    ("country", self.country),
                    ("website", self.website),
                ) if val is not None
            ]
            if extra:
                raise ValueError(
                    "company_id was given alongside new-company fields: "
                    f"{extra}. Send either company_id OR the new-company "
                    "fields, not both."
                )
            return self

        # New-company branch — require the three mandatory fields.
        missing = [
            name for name, val in (
                ("company_name", self.company_name),
                ("company_description", self.company_description),
                ("country", self.country),
            )
            if not val or not val.strip()
        ]
        if missing:
            raise ValueError(
                "Provide either company_id, or all of company_name + "
                f"company_description + country. Missing: {missing}"
            )
        return self

    def as_new_company_fields(self) -> NewCompanyFields:
        """Return the validated new-company subset. Only safe to call when
        ``company_id`` is None — caller enforces."""
        return NewCompanyFields(
            company_name=self.company_name or "",
            company_description=self.company_description or "",
            country=self.country or "",
            website=self.website,
        )

    def forwarded_payload(self, *, company_id: str) -> dict[str, Any]:
        """Payload to send on to n8n. Always keyed on a concrete
        ``company_id``; any new-company fields are stripped out because
        the downstream workflow only needs the resolved record ID."""
        base = self.model_dump(exclude_none=True)
        for key in (
            "company_name",
            "company_description",
            "country",
            "website",
        ):
            base.pop(key, None)
        base["company_id"] = company_id
        return base
