"""Tests for GET /v1/searches/{job_id}/matches.

Reuses the FakeRepo from test_searches (it exposes the same dependency
override) and seeds the in-memory ``matches_by_job`` dict with Airtable-
shaped rows.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.models.jobs import JobStatus

from .test_searches import (
    FAKE_REPO,
    PARTNER_KEY_PLAINTEXT,
    PARTNER_RECORD_ID,
)

client = TestClient(app)

# A small but representative sample of the real payload shapes that n8n
# writes. Keeps the tests readable without blowing up fixture size.
RAW_JSON_OK = {
    "Short Grant Title": "Centres of Vocational Excellence",
    "Match Level": "Eligible",
    "Match Summary": "Strong technical contender.",
    "Activity Fit Level": "Strong Fit",
}
GRANT_JSON_OK = {
    "core_metadata": {
        "grant_name": "Centres of Vocational Excellence",
        "reference_id": "ERASMUS-EDU-2026-PEX-COVE",
    },
    "financials": {"currency": "EUR", "overall_total_call_budget": 68_000_000},
}


def _seed_done_job_with_matches(
    rows: list[dict] | None = None, partner_record_id: str = PARTNER_RECORD_ID
) -> UUID:
    """Create a fake DONE search job owned by the test partner, optionally
    seeded with Airtable-shaped match rows."""
    job_id = uuid4()
    FAKE_REPO.create_job(
        job_id=job_id,
        api_key_record_id=partner_record_id,
        workflow_kind=__import__("app.models.jobs", fromlist=["WorkflowKind"]).WorkflowKind.SEARCH,
        request_payload={"organisation": "AcmeBio"},
    )
    # Flip to DONE manually (bypasses the callback path for brevity).
    j = FAKE_REPO.jobs[job_id]
    j.status = JobStatus.DONE.value
    if rows is not None:
        FAKE_REPO.matches_by_job[job_id] = rows
    return job_id


# ---------------------------------------------------------------------------
# Auth / ownership
# ---------------------------------------------------------------------------
def test_matches_requires_auth():
    r = client.get(f"/v1/searches/{uuid4()}/matches")
    assert r.status_code == 401


def test_matches_unknown_job_404():
    r = client.get(
        f"/v1/searches/{uuid4()}/matches",
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r.status_code == 404


def test_matches_other_partners_job_is_404():
    job_id = _seed_done_job_with_matches(
        rows=[], partner_record_id="recOTHER_PARTNER"
    )
    r = client.get(
        f"/v1/searches/{job_id}/matches",
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    # 404 not 403 — don't leak existence of other partners' job IDs.
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Status gating
# ---------------------------------------------------------------------------
def test_matches_queued_returns_409(monkeypatch):
    async def fake_fire(self, *, job_id, payload):  # noqa: ANN001
        return "exec_q"

    monkeypatch.setattr("app.services.n8n.N8nClient.fire_search", fake_fire)

    # Create via the real POST so status starts as queued.
    r = client.post(
        "/v1/searches",
        json={"payload": {"organisation": "AcmeBio"}},
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    job_id = r.json()["job_id"]

    r2 = client.get(
        f"/v1/searches/{job_id}/matches",
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r2.status_code == 409
    body = r2.json()
    assert body["error"]["code"] == "JOB_NOT_READY"
    details = body["error"]["details"]
    assert details["status"] in {"queued", "running"}
    assert "hint" in details


def test_matches_failed_returns_409_with_error():
    job_id = _seed_done_job_with_matches(rows=[])
    j = FAKE_REPO.jobs[job_id]
    j.status = JobStatus.FAILED.value
    j.error = "n8n blew up"

    r = client.get(
        f"/v1/searches/{job_id}/matches",
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r.status_code == 409
    body = r.json()
    assert body["error"]["code"] == "JOB_FAILED"
    assert body["error"]["details"]["job_error"] == "n8n blew up"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
def test_matches_empty_list():
    job_id = _seed_done_job_with_matches(rows=[])
    r = client.get(
        f"/v1/searches/{job_id}/matches",
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["job_id"] == str(job_id)
    assert body["count"] == 0
    assert body["matches"] == []


def test_matches_returns_parsed_nested_objects():
    rows = [
        {
            "id": "recMATCH001",
            "fields": {
                "api_job_id": "placeholder",
                "Raw Json": json.dumps(RAW_JSON_OK),
                # Lookup fields come back as a list.
                "Grant Details JSON": [json.dumps(GRANT_JSON_OK)],
                "Match Id": "mat-001",
                "Created Time": "2026-04-22T10:30:00.000Z",
            },
        },
        {
            "id": "recMATCH002",
            "fields": {
                "api_job_id": "placeholder",
                "Raw Json": json.dumps(
                    {**RAW_JSON_OK, "Match Level": "Partial"}
                ),
                "Grant Details JSON": [json.dumps(GRANT_JSON_OK)],
                "Match Id": "mat-002",
                "Created Time": "2026-04-22T10:35:00.000Z",
            },
        },
    ]
    job_id = _seed_done_job_with_matches(rows=rows)

    r = client.get(
        f"/v1/searches/{job_id}/matches",
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2
    first = body["matches"][0]
    # Nested not flattened.
    assert first["match"]["Short Grant Title"] == "Centres of Vocational Excellence"
    assert first["grant"]["core_metadata"]["reference_id"] == "ERASMUS-EDU-2026-PEX-COVE"
    assert first["match_id"] == "mat-001"
    assert first["created_at"].startswith("2026-04-22T10:30:00")


def test_matches_surfaces_parse_error_instead_of_dropping_row():
    rows = [
        {
            "id": "recMATCH_BAD",
            "fields": {
                "api_job_id": "placeholder",
                "Raw Json": "{not valid json",
                "Grant Details JSON": [json.dumps(GRANT_JSON_OK)],
                "Match Id": "mat-bad",
                "Created Time": "2026-04-22T10:30:00.000Z",
            },
        }
    ]
    job_id = _seed_done_job_with_matches(rows=rows)

    r = client.get(
        f"/v1/searches/{job_id}/matches",
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    m = body["matches"][0]["match"]
    assert "_parse_error" in m
    assert m["_raw"] == "{not valid json"
    # The good half (grant) still parses normally.
    assert body["matches"][0]["grant"]["core_metadata"]["reference_id"]


def test_matches_handles_empty_lookup_and_missing_fields():
    rows = [
        {
            "id": "recMATCH_EMPTY",
            "fields": {
                # Row exists but some fields are absent (Airtable omits empty).
                "api_job_id": "placeholder",
                "Match Id": "mat-empty",
            },
        }
    ]
    job_id = _seed_done_job_with_matches(rows=rows)

    r = client.get(
        f"/v1/searches/{job_id}/matches",
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["matches"][0]["match"] == {}
    assert body["matches"][0]["grant"] == {}
