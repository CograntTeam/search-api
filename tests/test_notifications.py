"""Tests for the daily digest: rendering, the notification gate, and statuses."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.config import Settings
from app.services.notifications import NotificationService, render_digest
from app.services.smtp_client import SmtpError


def _settings(**overrides: Any) -> Settings:
    base = dict(
        email_enabled=True,
        email_dry_run=False,
        smtp_username="bot@cogrant.eu",
        smtp_password="app-password",
        email_from="info@cogrant.eu",
        email_bcc="info@cogrant.eu",
    )
    base.update(overrides)
    return Settings(**base)  # required fields come from the test env (conftest)


def _match(
    match_id: str,
    company_id: str,
    *,
    title: str = "Eurostars Call 11",
    type_: str | None = None,
    grant_details: dict | None = None,
    grant_size: str | None = None,
    grant_deadline: str | None = None,
) -> dict[str, Any]:
    raw = {
        "Hook sentence": "Scale across Europe.",
        "Match Summary": "Strong candidate for international R&D.",
        "Match Level": "Eligible",
        "formatted_fits": "🟢 Eligibility: Eligible\n🟢 Objective: Strong fit",
    }
    fields: dict[str, Any] = {
        "Company": [company_id],
        "Name": title,
        "Match Description": "Scale across Europe.\n\nStrong candidate.",
        "Raw Json": json.dumps(raw),
    }
    if type_ is not None:
        fields["Type"] = type_
    # Airtable lookup fields arrive as a single-element list.
    if grant_size is not None:
        fields["Grant Size"] = [grant_size]
    if grant_deadline is not None:
        fields["Grant Deadline"] = [grant_deadline]
    if grant_details is not None:
        fields["Grant Details JSON"] = [json.dumps(grant_details)]
    return {"id": match_id, "fields": fields}


_GRANT_DETAILS = {
    "core_metadata": {
        "short_grant_name": "Smart Specialisation R&D",
        "managing_authority": "Innovation Agency Lithuania",
        "program_name": "RIS3",
    },
    "financials": {
        "currency": "EUR",
        "funding_tiers": [{"minimum_funding_ticket": 100000, "maximum_funding_ticket": 300000}],
    },
    "timelines": {"application_stages": [{"deadline_date": "2030-09-15"}]},
    "eligibility_and_consortia": {"geography_level": "National"},
}


class FakeRepo:
    def __init__(self, matches: list[dict], companies: dict[str, dict]) -> None:
        self._matches = matches
        self._companies = companies
        self.status_updates: list[tuple[str, str]] = []

    def list_pending_notification_matches(self) -> list[dict]:
        return list(self._matches)

    def get_companies_by_ids(self, ids: list[str]) -> dict[str, dict]:
        return {i: self._companies[i] for i in ids if i in self._companies}

    def set_match_notification_status(self, match_id: str, status: str) -> None:
        self.status_updates.append((match_id, status))


class FakeSmtp:
    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[dict[str, Any]] = []
        self.fail = fail

    async def send(self, *, to, subject, text_body, html_body, bcc=None) -> None:
        if self.fail:
            raise SmtpError("transport boom")
        self.sent.append({"to": to, "subject": subject, "bcc": bcc, "html": html_body})


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------
def test_render_digest_contents():
    settings = _settings()
    subject, text, html = render_digest("Acme Bio", [_match("m1", "c1")], settings)
    assert subject == "1 new opportunity matched to Acme Bio"
    assert "Eurostars Call 11" in text
    assert "Scale across Europe" in text
    assert "Eurostars Call 11" in html
    assert "planner.cogrant.eu" in html


def test_render_digest_pluralisation():
    subject, _, _ = render_digest("Acme", [_match("m1", "c1"), _match("m2", "c1")], _settings())
    assert subject == "2 new opportunities matched to Acme"


def test_render_digest_full_card_from_grant_details():
    _, _, html = render_digest(
        "Acme",
        [_match("m1", "c1", type_="Quick Win", grant_details=_GRANT_DETAILS)],
        _settings(),
    )
    assert "Smart Specialisation R&amp;D" in html  # short_grant_name overrides Name
    assert "Innovation Agency Lithuania · RIS3" in html
    assert "€100K–€300K" in html
    assert "15 Sep 2030" in html
    assert "days left" in html
    assert "Quick Win · National" in html


def test_render_digest_prefers_grant_size_and_deadline_fields():
    # The card reads the grant's ready-made "Grant Size" / "Grant Deadline" lookups
    # in preference to anything derived from the Grant Details JSON blob.
    _, _, html = render_digest(
        "Acme",
        [
            _match(
                "m1",
                "c1",
                grant_size="Up to 500k€",
                grant_deadline="2030-09-10",
                grant_details=_GRANT_DETAILS,
            )
        ],
        _settings(),
    )
    assert "Up to 500k€" in html  # dedicated field used verbatim
    assert "€100K–€300K" not in html  # gd-derived funding is overridden
    assert "10 Sep 2030" in html  # dedicated deadline
    assert "15 Sep 2030" not in html  # gd-derived deadline is overridden
    assert "days left" in html


def test_render_digest_falls_back_to_grant_details_when_fields_absent():
    # No dedicated fields -> derive funding/deadline from the Grant Details JSON.
    _, _, html = render_digest(
        "Acme",
        [_match("m1", "c1", grant_details=_GRANT_DETAILS)],
        _settings(),
    )
    assert "€100K–€300K" in html
    assert "15 Sep 2030" in html


def test_render_digest_degrades_without_grant_details():
    _, _, html = render_digest("Acme", [_match("m1", "c1")], _settings())
    assert "Eurostars Call 11" in html  # falls back to the match Name
    assert "Scale across Europe" in html  # description still renders
    assert "planner.cogrant.eu" in html
    # No enrichment -> no funding/deadline tokens, nothing fabricated.
    assert "days left" not in html
    assert "€" not in html
    assert "Up to" not in html


def test_render_digest_greeting_uses_first_name():
    _, text, html = render_digest("Acme", [_match("m1", "c1")], _settings(), first_name="Rūta")
    assert "Hi Rūta -" in html
    assert "Hi Rūta," in text


def test_render_digest_greeting_without_first_name():
    _, _, html = render_digest("Acme", [_match("m1", "c1")], _settings())
    assert "Hi there -" in html


# ---------------------------------------------------------------------------
# gating + statuses
# ---------------------------------------------------------------------------
async def test_enabled_company_gets_email_and_marks_sent():
    repo = FakeRepo(
        [_match("m1", "c1")],
        {"c1": {"Company name": "Acme", "Email": "team@acme.com", "Notification Customer": "Pro"}},
    )
    smtp = FakeSmtp()
    await NotificationService(repo, smtp, _settings()).run_daily()
    assert len(smtp.sent) == 1
    assert smtp.sent[0]["to"] == "team@acme.com"
    assert smtp.sent[0]["bcc"] == "info@cogrant.eu"
    assert repo.status_updates == [("m1", "Sent")]


async def test_disabled_company_is_skipped_not_emailed():
    repo = FakeRepo(
        [_match("m1", "c1")],
        {"c1": {"Company name": "Acme", "Email": "team@acme.com", "Notification Customer": "False"}},
    )
    smtp = FakeSmtp()
    await NotificationService(repo, smtp, _settings()).run_daily()
    assert smtp.sent == []
    assert repo.status_updates == [("m1", "Skipped")]


@pytest.mark.parametrize("tier", ["Basic", "Custom"])
async def test_non_pro_tiers_are_skipped(tier):
    # Only Pro is notification-enabled now; Basic/Custom are skipped, not emailed.
    repo = FakeRepo(
        [_match("m1", "c1")],
        {"c1": {"Company name": "Acme", "Email": "team@acme.com", "Notification Customer": tier}},
    )
    smtp = FakeSmtp()
    await NotificationService(repo, smtp, _settings()).run_daily()
    assert smtp.sent == []
    assert repo.status_updates == [("m1", "Skipped")]


async def test_enabled_company_without_email_is_skipped():
    repo = FakeRepo(
        [_match("m1", "c1")],
        {"c1": {"Company name": "Acme", "Email": "", "Notification Customer": "Pro"}},
    )
    smtp = FakeSmtp()
    await NotificationService(repo, smtp, _settings()).run_daily()
    assert smtp.sent == []
    assert repo.status_updates == [("m1", "Skipped")]


async def test_dry_run_routes_to_sender_address():
    repo = FakeRepo(
        [_match("m1", "c1")],
        {"c1": {"Company name": "Acme", "Email": "team@acme.com", "Notification Customer": "Pro"}},
    )
    smtp = FakeSmtp()
    await NotificationService(repo, smtp, _settings(email_dry_run=True)).run_daily()
    assert smtp.sent[0]["to"] == "info@cogrant.eu"


async def test_send_failure_marks_failed():
    repo = FakeRepo(
        [_match("m1", "c1")],
        {"c1": {"Company name": "Acme", "Email": "team@acme.com", "Notification Customer": "Pro"}},
    )
    smtp = FakeSmtp(fail=True)
    await NotificationService(repo, smtp, _settings()).run_daily()
    assert repo.status_updates == [("m1", "Failed")]


async def test_two_matches_one_company_single_email():
    repo = FakeRepo(
        [_match("m1", "c1"), _match("m2", "c1", title="Horizon EIC")],
        {"c1": {"Company name": "Acme", "Email": "team@acme.com", "Notification Customer": "Pro"}},
    )
    smtp = FakeSmtp()
    await NotificationService(repo, smtp, _settings()).run_daily()
    assert len(smtp.sent) == 1  # one digest, not one per match
    assert {s for _, s in repo.status_updates} == {"Sent"}
    assert len(repo.status_updates) == 2
