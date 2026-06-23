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


def _match(match_id: str, company_id: str, *, title: str = "Eurostars Call 11") -> dict[str, Any]:
    raw = {
        "Hook sentence": "Scale across Europe.",
        "Match Summary": "Strong candidate for international R&D.",
        "formatted_fits": "🟢 Eligibility: Eligible\n🟢 Objective: Strong fit",
    }
    return {
        "id": match_id,
        "fields": {
            "Company": [company_id],
            "Name": title,
            "Match Description": "Scale across Europe.\n\nStrong candidate.",
            "Raw Json": json.dumps(raw),
        },
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
    assert "1 new grant match" in subject
    assert "Acme Bio" in subject
    assert "Eurostars Call 11" in text
    assert "Scale across Europe" in text
    assert "Eurostars Call 11" in html
    assert "🟢 Objective: Strong fit" in html


def test_render_digest_pluralisation():
    subject, _, _ = render_digest("Acme", [_match("m1", "c1"), _match("m2", "c1")], _settings())
    assert "2 new grant matches" in subject


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
