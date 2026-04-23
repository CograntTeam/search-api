"""Tests for the unified error envelope, request-ID middleware, and
idempotency replay header. Reuses the in-memory FakeRepo from test_searches.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app

from .test_searches import FAKE_REPO, PARTNER_KEY_PLAINTEXT  # noqa: F401

client = TestClient(app)


# ---------------------------------------------------------------------------
# Envelope shape
# ---------------------------------------------------------------------------
def test_error_envelope_has_code_message_request_id_on_401():
    r = client.get(f"/v1/searches/{uuid4()}/matches")
    assert r.status_code == 401
    body = r.json()
    assert "error" in body
    err = body["error"]
    assert err["code"] == "UNAUTHORIZED"
    assert isinstance(err["message"], str) and err["message"]
    assert err["request_id"].startswith("req_")
    # details is optional — absent here.
    assert "details" not in err or err["details"] is None


def test_error_envelope_on_404_job_not_found():
    r = client.get(
        f"/v1/searches/{uuid4()}",
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "JOB_NOT_FOUND"


def test_error_envelope_on_422_validation():
    # Body missing required `payload` key entirely.
    r = client.post(
        "/v1/searches",
        json={},
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "INVALID_REQUEST"
    # Pydantic's per-field errors are surfaced under details.
    assert "field_errors" in body["error"]["details"]
    assert any(
        "payload" in tuple(fe.get("loc", ()))
        for fe in body["error"]["details"]["field_errors"]
    )


def test_error_envelope_on_422_empty_payload():
    # Body has `payload` but it's empty → our explicit APIError branch.
    r = client.post(
        "/v1/searches",
        json={"payload": {}},
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_REQUEST"


# ---------------------------------------------------------------------------
# Request ID middleware
# ---------------------------------------------------------------------------
def test_response_carries_request_id_header():
    r = client.get("/health")
    assert "x-request-id" in {h.lower() for h in r.headers}
    assert r.headers["x-request-id"].startswith("req_")


def test_incoming_request_id_is_honoured():
    custom = "req_client_supplied_42"
    r = client.get("/health", headers={"X-Request-Id": custom})
    assert r.headers["x-request-id"] == custom


def test_error_envelope_request_id_matches_response_header():
    r = client.get(f"/v1/searches/{uuid4()}/matches")
    assert r.status_code == 401
    assert r.json()["error"]["request_id"] == r.headers["x-request-id"]


# ---------------------------------------------------------------------------
# Idempotency replay header
# ---------------------------------------------------------------------------
def test_idempotency_replay_sets_header(monkeypatch):
    async def fake_fire(self, *, job_id, payload):  # noqa: ANN001
        return "exec_idem_hdr"

    monkeypatch.setattr("app.services.n8n.N8nClient.fire_search", fake_fire)
    headers = {
        "Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}",
        "Idempotency-Key": "idem-hdr-xyz",
    }
    body = {"payload": {"company_id": "recX"}}
    r1 = client.post("/v1/searches", json=body, headers=headers)
    assert r1.status_code == 202
    # First call is a real create — no replay header.
    assert "idempotency-replayed" not in {h.lower() for h in r1.headers.keys()}

    r2 = client.post("/v1/searches", json=body, headers=headers)
    assert r2.status_code == 202
    assert r2.headers.get("Idempotency-Replayed") == "true"
    assert r2.json()["job_id"] == r1.json()["job_id"]
