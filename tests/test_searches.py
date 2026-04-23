"""Tests for /v1/searches and the internal completion callback.

We stub AirtableRepo with an in-memory fake via FastAPI's dependency override
system, and stub httpx so no real network is touched.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.models.jobs import Job, JobStatus, WorkflowKind
from app.models.keys import ApiKey, KeyStatus
from app.repositories.airtable import AirtableRepo
from app.security import get_repo

SECRET = "test_secret_value_long_enough_yes"
PARTNER_RECORD_ID = "recPARTNER000001"
PARTNER_KEY_PLAINTEXT = "sk_test_partner_key_123"
# sha256 of the plaintext above, hex-lowercased
PARTNER_KEY_HASH = __import__("hashlib").sha256(
    PARTNER_KEY_PLAINTEXT.encode()
).hexdigest()


class FakeRepo:
    """In-memory AirtableRepo stand-in."""

    def __init__(self) -> None:
        self.jobs: dict[UUID, Job] = {}
        self.key = ApiKey(
            record_id=PARTNER_RECORD_ID,
            partner_name="Test Partner",
            key_hash=PARTNER_KEY_HASH,
            key_prefix="sk_test_",
            status=KeyStatus.ACTIVE,
            rate_limit_per_min=60,
        )

    # --- keys ------------------------------------------------------------
    def find_key_by_hash(self, key_hash: str) -> ApiKey | None:
        return self.key if key_hash == PARTNER_KEY_HASH else None

    def touch_key_last_used(self, record_id: str) -> None:
        return None

    # --- jobs ------------------------------------------------------------
    def create_job(
        self,
        *,
        job_id: UUID,
        api_key_record_id: str,
        workflow_kind: WorkflowKind,
        request_payload: dict[str, Any],
        callback_url: str | None = None,
        idempotency_key: str | None = None,
    ) -> Job:
        now = datetime.now(UTC)
        job = Job(
            job_id=job_id,
            api_key_record_id=api_key_record_id,
            workflow_kind=workflow_kind,
            status=JobStatus.QUEUED,
            request_payload=request_payload,
            callback_url=callback_url,
            idempotency_key=idempotency_key,
            created_at=now,
            updated_at=now,
        )
        self.jobs[job_id] = job
        return job

    def get_job(self, job_id: UUID) -> Job | None:
        return self.jobs.get(job_id)

    def find_job_by_idempotency(
        self, api_key_record_id: str, idempotency_key: str
    ) -> Job | None:
        for j in self.jobs.values():
            if (
                j.api_key_record_id == api_key_record_id
                and j.idempotency_key == idempotency_key
            ):
                return j
        return None

    def set_job_running(self, job_id: UUID, n8n_execution_id: str | None = None) -> None:
        j = self.jobs.get(job_id)
        if j is None:
            return
        j.status = JobStatus.RUNNING.value
        j.n8n_execution_id = n8n_execution_id
        j.updated_at = datetime.now(UTC)

    def complete_job(
        self,
        job_id: UUID,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> Job | None:
        j = self.jobs.get(job_id)
        if j is None:
            return None
        now = datetime.now(UTC)
        j.status = (JobStatus.FAILED if error else JobStatus.DONE).value
        j.result = result
        j.error = error
        j.updated_at = now
        j.completed_at = now
        return j


# Single shared fake across tests so /v1/searches POST + GET see the same state.
FAKE_REPO = FakeRepo()


def _get_fake_repo() -> AirtableRepo:  # type: ignore[return-value]
    return FAKE_REPO  # duck-typed; routers only call the documented methods


app.dependency_overrides[get_repo] = _get_fake_repo
client = TestClient(app)


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------
def test_missing_key_returns_401():
    r = client.post("/v1/searches", json={"payload": {"foo": "bar"}})
    assert r.status_code == 401


def test_wrong_key_returns_401():
    r = client.post(
        "/v1/searches",
        json={"payload": {"foo": "bar"}},
        headers={"Authorization": "Bearer not_a_real_key"},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /v1/searches
# ---------------------------------------------------------------------------
def test_create_search_returns_202(monkeypatch):
    # Stub n8n so no real HTTP happens.
    async def fake_fire(self, *, job_id, payload):  # noqa: ANN001
        return "exec_123"

    monkeypatch.setattr(
        "app.services.n8n.N8nClient.fire_search", fake_fire
    )

    r = client.post(
        "/v1/searches",
        json={"payload": {"organisation": "AcmeBio", "topic": "green-hydrogen"}},
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "queued"
    UUID(body["job_id"])  # parses


def test_empty_payload_422(monkeypatch):
    r = client.post(
        "/v1/searches",
        json={"payload": {}},
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------
def test_idempotency_returns_same_job(monkeypatch):
    async def fake_fire(self, *, job_id, payload):  # noqa: ANN001
        return "exec_idem"

    monkeypatch.setattr("app.services.n8n.N8nClient.fire_search", fake_fire)

    headers = {
        "Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}",
        "Idempotency-Key": "idem-abc-123",
    }
    body = {"payload": {"organisation": "AcmeBio"}}
    r1 = client.post("/v1/searches", json=body, headers=headers)
    r2 = client.post("/v1/searches", json=body, headers=headers)
    assert r1.status_code == 202
    assert r2.status_code == 202
    assert r1.json()["job_id"] == r2.json()["job_id"]


# ---------------------------------------------------------------------------
# GET /v1/searches/{id}
# ---------------------------------------------------------------------------
def test_get_search_404_when_unknown():
    r = client.get(
        f"/v1/searches/{uuid4()}",
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r.status_code == 404


def test_get_search_returns_job(monkeypatch):
    async def fake_fire(self, *, job_id, payload):  # noqa: ANN001
        return "exec_get"

    monkeypatch.setattr("app.services.n8n.N8nClient.fire_search", fake_fire)

    r = client.post(
        "/v1/searches",
        json={"payload": {"organisation": "AcmeBio"}},
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    job_id = r.json()["job_id"]
    r2 = client.get(
        f"/v1/searches/{job_id}",
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["job_id"] == job_id
    assert body["workflow_kind"] == "search"
    assert body["status"] in {"queued", "running", "done", "failed"}


# ---------------------------------------------------------------------------
# Internal callback
# ---------------------------------------------------------------------------
def test_internal_callback_requires_secret():
    r = client.post(
        f"/internal/jobs/{uuid4()}/complete",
        json={"result": {"matches": []}},
    )
    assert r.status_code == 401


def test_internal_callback_completes_job(monkeypatch):
    async def fake_fire(self, *, job_id, payload):  # noqa: ANN001
        return "exec_cb"

    monkeypatch.setattr("app.services.n8n.N8nClient.fire_search", fake_fire)

    r = client.post(
        "/v1/searches",
        json={"payload": {"organisation": "AcmeBio"}},
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    job_id = r.json()["job_id"]

    r2 = client.post(
        f"/internal/jobs/{job_id}/complete",
        json={"result": {"matches": [{"call_id": "abc", "score": 0.9}]}},
        headers={"X-Internal-Secret": SECRET},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "done"

    # Replay should be idempotent.
    r3 = client.post(
        f"/internal/jobs/{job_id}/complete",
        json={"result": {"matches": []}},
        headers={"X-Internal-Secret": SECRET},
    )
    assert r3.status_code == 200
    assert r3.json()["idempotent"] == "true"


# ---------------------------------------------------------------------------
# Match-check endpoints — share the exact same shape as /v1/searches,
# so we only need a thin sanity check per variant.
# ---------------------------------------------------------------------------
def test_match_check_a_create_and_get(monkeypatch):
    async def fake_fire(self, *, job_id, payload):  # noqa: ANN001
        return "exec_a"

    monkeypatch.setattr(
        "app.services.n8n.N8nClient.fire_match_check_a", fake_fire
    )

    r = client.post(
        "/v1/match-checks/a",
        json={"payload": {"company_id": "recABC123", "call_ids": ["x", "y"]}},
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]

    r2 = client.get(
        f"/v1/match-checks/a/{job_id}",
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["workflow_kind"] == "match_check_a"

    # Wrong endpoint for this job kind → 404 (not 403) to avoid leaking existence.
    r3 = client.get(
        f"/v1/match-checks/b/{job_id}",
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r3.status_code == 404

    r4 = client.get(
        f"/v1/searches/{job_id}",
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r4.status_code == 404


def test_match_check_b_create_and_get(monkeypatch):
    async def fake_fire(self, *, job_id, payload):  # noqa: ANN001
        return "exec_b"

    monkeypatch.setattr(
        "app.services.n8n.N8nClient.fire_match_check_b", fake_fire
    )

    r = client.post(
        "/v1/match-checks/b",
        json={"payload": {"company_id": "recABC123", "call_ids": ["z"]}},
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]

    r2 = client.get(
        f"/v1/match-checks/b/{job_id}",
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r2.status_code == 200
    assert r2.json()["workflow_kind"] == "match_check_b"


def test_match_check_a_requires_auth():
    r = client.post(
        "/v1/match-checks/a", json={"payload": {"company_id": "recX"}}
    )
    assert r.status_code == 401


def test_match_check_empty_payload_422(monkeypatch):
    r = client.post(
        "/v1/match-checks/a",
        json={"payload": {}},
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r.status_code == 422


def test_match_check_a_idempotency(monkeypatch):
    async def fake_fire(self, *, job_id, payload):  # noqa: ANN001
        return "exec_a_idem"

    monkeypatch.setattr(
        "app.services.n8n.N8nClient.fire_match_check_a", fake_fire
    )

    headers = {
        "Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}",
        "Idempotency-Key": "mc-a-idem-1",
    }
    body = {"payload": {"company_id": "recABC"}}
    r1 = client.post("/v1/match-checks/a", json=body, headers=headers)
    r2 = client.post("/v1/match-checks/a", json=body, headers=headers)
    assert r1.status_code == 202
    assert r2.status_code == 202
    assert r1.json()["job_id"] == r2.json()["job_id"]


def test_internal_callback_records_failure(monkeypatch):
    async def fake_fire(self, *, job_id, payload):  # noqa: ANN001
        return None

    monkeypatch.setattr("app.services.n8n.N8nClient.fire_search", fake_fire)

    r = client.post(
        "/v1/searches",
        json={"payload": {"organisation": "AcmeBio"}},
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    job_id = r.json()["job_id"]

    r2 = client.post(
        f"/internal/jobs/{job_id}/complete",
        json={"error": "n8n blew up"},
        headers={"X-Internal-Secret": SECRET},
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "failed"

    r3 = client.get(
        f"/v1/searches/{job_id}",
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r3.json()["status"] == "failed"
    assert r3.json()["error"] == "n8n blew up"
