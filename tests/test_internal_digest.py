"""Manual digest trigger — POST /internal/digest/run.

Guarded by the internal shared secret (like the other /internal routes) and runs
the same work as the 15:00 scheduler job, so emails can be tested on demand.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services.notifications import NotificationService

client = TestClient(app)

# Matches conftest's INTERNAL_SHARED_SECRET.
SECRET = "test_secret_value_long_enough_yes"


def test_digest_run_requires_secret():
    r = client.post("/internal/digest/run")
    assert r.status_code == 401


def test_digest_run_triggers_service(monkeypatch):
    captured: dict = {}

    async def fake_run_daily(self, *, dry_run=None):  # noqa: ANN001
        captured["dry_run"] = dry_run
        return {"companies": 2, "sent": 2, "skipped": 0, "failed": 0}

    monkeypatch.setattr(NotificationService, "run_daily", fake_run_daily)

    r = client.post("/internal/digest/run", headers={"X-Internal-Secret": SECRET})
    assert r.status_code == 200, r.text
    assert r.json() == {"companies": 2, "sent": 2, "skipped": 0, "failed": 0}
    # No dry_run query param → honour the global EMAIL_DRY_RUN setting.
    assert captured["dry_run"] is None


def test_digest_run_dry_run_forces_override(monkeypatch):
    captured: dict = {}

    async def fake_run_daily(self, *, dry_run=None):  # noqa: ANN001
        captured["dry_run"] = dry_run
        return {"companies": 0, "sent": 0, "skipped": 0, "failed": 0}

    monkeypatch.setattr(NotificationService, "run_daily", fake_run_daily)

    r = client.post(
        "/internal/digest/run?dry_run=true", headers={"X-Internal-Secret": SECRET}
    )
    assert r.status_code == 200, r.text
    assert captured["dry_run"] is True
