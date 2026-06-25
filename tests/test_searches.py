"""Tests for /v1/searches and the internal completion callback.

We stub AirtableRepo with an in-memory fake via FastAPI's dependency override
system. The forward-search engine itself is patched out here (see
``_patch_forward_search``); its behaviour is covered in ``test_forward_search.py``.
This file focuses on the HTTP/job layer: auth, validation, idempotency, the
dispatch wiring, and the internal completion endpoint.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
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
            rate_limit_per_day=500,
            rate_limit_per_week=2000,
            searches_per_day=50,
            searches_per_week=200,
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

    # --- search matches --------------------------------------------------
    matches_by_job: dict[UUID, list[dict[str, Any]]] = {}

    def list_matches_for_job(self, api_job_id: UUID) -> list[dict[str, Any]]:
        return list(self.matches_by_job.get(api_job_id, []))

    # --- companies --------------------------------------------------------
    created_companies: list[dict[str, Any]] = []

    def create_company(
        self,
        *,
        name: str,
        description: str,
        country: str,
        website: str | None = None,
        organisation_type: str = "Private Business",
        lead_source: str = "Search API",
    ) -> str:
        new_id = f"recCOMPANY{len(self.created_companies):06d}"
        self.created_companies.append(
            {
                "id": new_id,
                "name": name,
                "description": description,
                "country": country,
                "website": website,
                "organisation_type": organisation_type,
                "lead_source": lead_source,
            }
        )
        return new_id


# Single shared fake across tests so /v1/searches POST + GET see the same state.
FAKE_REPO = FakeRepo()


def _get_fake_repo() -> AirtableRepo:  # type: ignore[return-value]
    return FAKE_REPO  # duck-typed; routers only call the documented methods


app.dependency_overrides[get_repo] = _get_fake_repo
client = TestClient(app)

# Captures what the dispatch handed to the (patched-out) forward search.
DISPATCHED: dict[str, Any] = {}


@pytest.fixture(autouse=True)
def _patch_forward_search(monkeypatch):
    """Stub the in-process forward search so the background task completes the job
    without touching Gemini/Airtable. The engine itself is tested separately."""
    DISPATCHED.clear()

    async def fake_run(self, company_id, *, api_job_id):  # noqa: ANN001
        DISPATCHED["company_id"] = company_id
        DISPATCHED["api_job_id"] = api_job_id
        return {"company_record_id": company_id, "matches_created": 0}

    monkeypatch.setattr(
        "app.services.forward_search.ForwardSearchService.run_for_company", fake_run
    )
    yield


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
# POST /v1/searches — existing-company path
# ---------------------------------------------------------------------------
def test_create_search_with_existing_company_id():
    before = len(FAKE_REPO.created_companies)
    r = client.post(
        "/v1/searches",
        json={"payload": {"company_id": "recEXISTINGCOMPANY"}},
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "queued"
    UUID(body["job_id"])

    # No new company should have been created on the existing-id path.
    assert len(FAKE_REPO.created_companies) == before
    # The dispatch runs the forward search for the submitted company_id.
    assert DISPATCHED["company_id"] == "recEXISTINGCOMPANY"
    assert str(DISPATCHED["api_job_id"]) == body["job_id"]


def test_empty_payload_422():
    r = client.post(
        "/v1/searches",
        json={"payload": {}},
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# POST /v1/searches — new-company path
# ---------------------------------------------------------------------------
def test_create_search_with_new_company():
    before = len(FAKE_REPO.created_companies)
    r = client.post(
        "/v1/searches",
        json={
            "payload": {
                "company_name": "Acme Bio",
                "company_description": "Fermentation-based protein.",
                "country": "Lithuania",
                "website": "https://acme.bio",
            }
        },
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r.status_code == 202, r.text
    # One company row created, with Private Business set automatically.
    assert len(FAKE_REPO.created_companies) == before + 1
    created = FAKE_REPO.created_companies[-1]
    assert created["name"] == "Acme Bio"
    assert created["description"] == "Fermentation-based protein."
    assert created["country"] == "Lithuania"
    assert created["website"] == "https://acme.bio"
    assert created["organisation_type"] == "Private Business"
    # Provenance tag so sales can spot self-serve API signups.
    assert created["lead_source"] == "Search API"

    # The forward search runs for the freshly-created record ID.
    assert DISPATCHED["company_id"] == created["id"]


def test_new_company_without_website():
    r = client.post(
        "/v1/searches",
        json={
            "payload": {
                "company_name": "No Website Co",
                "company_description": "Stealth mode.",
                "country": "Germany",
            }
        },
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r.status_code == 202, r.text
    assert FAKE_REPO.created_companies[-1]["website"] is None


def test_new_company_missing_country_is_422():
    r = client.post(
        "/v1/searches",
        json={
            "payload": {
                "company_name": "No Country Co",
                "company_description": "Forgot the country.",
            }
        },
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "INVALID_REQUEST"
    fe = body["error"]["details"]["field_errors"]
    assert any("country" in str(err).lower() for err in fe)


def test_cannot_mix_company_id_and_new_company_fields():
    r = client.post(
        "/v1/searches",
        json={
            "payload": {
                "company_id": "recEXISTING",
                "company_name": "Ambiguous",
                "company_description": "...",
                "country": "Lithuania",
            }
        },
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "INVALID_REQUEST"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------
def test_idempotency_returns_same_job():
    headers = {
        "Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}",
        "Idempotency-Key": "idem-abc-123",
    }
    body = {"payload": {"company_id": "recABCDEFGHIJKLMN"}}
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


def test_get_search_returns_job():
    r = client.post(
        "/v1/searches",
        json={"payload": {"company_id": "recABCDEFGHIJKLMN"}},
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
    # The in-process search completes during the (TestClient-run) background task.
    assert body["status"] == "done"
    assert body["company_id"] == "recABCDEFGHIJKLMN"


def test_get_search_surfaces_created_company_id():
    """New-company request: GET exposes the company_id the gateway created
    on the fly, so partners can capture the new record."""
    r = client.post(
        "/v1/searches",
        json={
            "payload": {
                "company_name": "Get Co",
                "company_description": "Created on the fly.",
                "country": "Germany",
            }
        },
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    created_id = FAKE_REPO.created_companies[-1]["id"]

    r2 = client.get(
        f"/v1/searches/{job_id}",
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["company_id"] == created_id


# ---------------------------------------------------------------------------
# Internal completion callback (still used by the n8n match-check workflows)
# ---------------------------------------------------------------------------
def _seed_running_job(company_id: str = "recABCDEFGHIJKLMN") -> UUID:
    job_id = uuid4()
    now = datetime.now(UTC)
    FAKE_REPO.jobs[job_id] = Job(
        job_id=job_id,
        api_key_record_id=PARTNER_RECORD_ID,
        workflow_kind=WorkflowKind.SEARCH,
        status=JobStatus.RUNNING,
        request_payload={"company_id": company_id},
        created_at=now,
        updated_at=now,
    )
    return job_id


def test_internal_callback_requires_secret():
    r = client.post(
        f"/internal/jobs/{uuid4()}/complete",
        json={"result": {"matches": []}},
    )
    assert r.status_code == 401


def test_internal_callback_completes_job():
    job_id = _seed_running_job()
    r2 = client.post(
        f"/internal/jobs/{job_id}/complete",
        json={"result": {"matches": [{"call_id": "abc", "score": 0.9}]}},
        headers={"X-Internal-Secret": SECRET},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "done"

    # Replay is idempotent.
    r3 = client.post(
        f"/internal/jobs/{job_id}/complete",
        json={"result": {"matches": []}},
        headers={"X-Internal-Secret": SECRET},
    )
    assert r3.status_code == 200
    assert r3.json()["idempotent"] == "true"


def test_internal_callback_records_failure():
    job_id = _seed_running_job()
    r2 = client.post(
        f"/internal/jobs/{job_id}/complete",
        json={"error": "workflow blew up"},
        headers={"X-Internal-Secret": SECRET},
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "failed"

    r3 = client.get(
        f"/v1/searches/{job_id}",
        headers={"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"},
    )
    assert r3.json()["status"] == "failed"
    assert r3.json()["error"] == "workflow blew up"
