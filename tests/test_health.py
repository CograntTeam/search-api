"""Smoke test — verifies the app boots and /health responds."""

import os

# Set env so Settings() succeeds inside the test environment.
os.environ.setdefault("AIRTABLE_PAT", "pat_test_dummy_value_for_ci")
os.environ.setdefault("AIRTABLE_BASE_ID", "apphC0wbp5dYfACfb")
os.environ.setdefault("AIRTABLE_API_KEYS_TABLE_ID", "tblfXzQKso559HNlQ")
os.environ.setdefault("AIRTABLE_API_JOBS_TABLE_ID", "tbl5QazdvtAVbAHZO")
os.environ.setdefault("N8N_WEBHOOK_BASE_URL", "https://example.n8n.cloud/webhook")
os.environ.setdefault("INTERNAL_SHARED_SECRET", "test_secret_value_long_enough_yes")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


def test_health_returns_ok():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["env"] in {"development", "staging", "production"}
    assert "version" in body


def test_openapi_available():
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert r.json()["info"]["title"] == "Cogrant Search API"
