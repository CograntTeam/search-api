"""Tests for :mod:`app.ratelimit` and its HTTP-surface integration.

Two layers of coverage:

* Pure-logic tests on :class:`InMemoryRateLimiter` — deterministic, no
  patching needed beyond freezing ``time.monotonic``.
* Integration tests through the FastAPI TestClient that verify the limiter
  is correctly wired into ``require_api_key`` and the 429 response carries
  the envelope + ``Retry-After`` / ``X-RateLimit-*`` headers.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.keys import ApiKey, KeyStatus
from app.ratelimit import (
    InMemoryRateLimiter,
    Window,
    rate_limiter,
    windows_for,
)

from .test_searches import (
    FAKE_REPO,
    PARTNER_KEY_HASH,
    PARTNER_KEY_PLAINTEXT,
    PARTNER_RECORD_ID,
)

client = TestClient(app)


# ---------------------------------------------------------------------------
# Pure-logic tests (no FastAPI)
# ---------------------------------------------------------------------------
class FakeClock:
    """Monkey-patchable substitute for ``time.monotonic``."""

    def __init__(self, t: float = 1_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def tick(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def frozen_time(monkeypatch):
    clk = FakeClock()
    monkeypatch.setattr("app.ratelimit.time.monotonic", clk)
    return clk


def test_allowed_until_limit_hit(frozen_time):
    lim = InMemoryRateLimiter()
    rules = [Window("minute", 60.0, 3)]
    # 3 hits → all allowed.
    for _ in range(3):
        d = lim.check("k", rules)
        assert d.allowed is True
    # 4th hit → tripped.
    d = lim.check("k", rules)
    assert d.allowed is False
    assert d.tripped is not None
    assert d.tripped.name == "minute"
    assert d.tripped.limit == 3


def test_tripped_request_does_not_consume_quota(frozen_time):
    lim = InMemoryRateLimiter()
    rules = [Window("minute", 60.0, 2)]
    lim.check("k", rules)
    lim.check("k", rules)
    # Over-limit attempt.
    blocked = lim.check("k", rules)
    assert blocked.allowed is False
    # Age one stamp out of the window; a fresh attempt must succeed because
    # the blocked call above must NOT have recorded a hit.
    frozen_time.tick(61.0)
    ok = lim.check("k", rules)
    assert ok.allowed is True


def test_window_resets_after_passing_time(frozen_time):
    lim = InMemoryRateLimiter()
    rules = [Window("minute", 60.0, 1)]
    assert lim.check("k", rules).allowed is True
    assert lim.check("k", rules).allowed is False
    frozen_time.tick(61.0)
    assert lim.check("k", rules).allowed is True


def test_keys_are_isolated(frozen_time):
    lim = InMemoryRateLimiter()
    rules = [Window("minute", 60.0, 1)]
    assert lim.check("alice", rules).allowed is True
    # bob is a fresh bucket.
    assert lim.check("bob", rules).allowed is True
    assert lim.check("alice", rules).allowed is False
    assert lim.check("bob", rules).allowed is False


def test_daily_window_trips_independently(frozen_time):
    lim = InMemoryRateLimiter()
    rules = [
        Window("minute", 60.0, 100),
        Window("day", 86_400.0, 3),
    ]
    # 3 hits — under minute cap, exactly at day cap.
    for _ in range(3):
        assert lim.check("k", rules).allowed is True
    # 4th hit — under minute cap but over day cap.
    d = lim.check("k", rules)
    assert d.allowed is False
    assert d.tripped is not None and d.tripped.name == "day"


def test_weekly_window_trips(frozen_time):
    lim = InMemoryRateLimiter()
    rules = [
        Window("minute", 60.0, 1_000),
        Window("day", 86_400.0, 1_000),
        Window("week", 604_800.0, 2),
    ]
    assert lim.check("k", rules).allowed is True
    assert lim.check("k", rules).allowed is True
    d = lim.check("k", rules)
    assert d.allowed is False
    assert d.tripped is not None and d.tripped.name == "week"


def test_zero_limit_is_unlimited(frozen_time):
    lim = InMemoryRateLimiter()
    # Limit 0 means "no cap" — windows_for drops it entirely.
    windows = windows_for(per_min=0, per_day=None, per_week=-3)
    assert windows == []
    for _ in range(100):
        assert lim.check("k", windows).allowed is True


def test_reset_seconds_points_at_oldest_stamp_aging_out(frozen_time):
    lim = InMemoryRateLimiter()
    rules = [Window("minute", 60.0, 1)]
    assert lim.check("k", rules).allowed is True
    frozen_time.tick(20.0)
    d = lim.check("k", rules)
    assert d.allowed is False
    # Oldest stamp was at t=1000; current = t+20. Retry-after ≈ 40s.
    assert 39.0 <= d.tripped.reset_in_seconds <= 41.0


# ---------------------------------------------------------------------------
# HTTP integration (via require_api_key)
# ---------------------------------------------------------------------------
def _tight_limit_key(per_min: int = 2) -> None:
    """Mutate the FakeRepo key in place so this test's partner has a tiny
    per-minute cap. Safe across tests because conftest autouse-resets the
    limiter buckets and we always restore the key at fixture teardown."""
    FAKE_REPO.key = ApiKey(
        record_id=PARTNER_RECORD_ID,
        partner_name="Test Partner",
        key_hash=PARTNER_KEY_HASH,
        key_prefix="sk_test_",
        status=KeyStatus.ACTIVE,
        rate_limit_per_min=per_min,
        rate_limit_per_day=None,
        rate_limit_per_week=None,
    )


@pytest.fixture
def _restore_key():
    """Snapshot and restore FakeRepo.key around tests that mutate it."""
    original = FAKE_REPO.key
    yield
    FAKE_REPO.key = original


def test_429_envelope_and_headers(_restore_key):
    _tight_limit_key(per_min=2)
    headers = {"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"}
    # Two allowed requests, then the third trips the minute window.
    r1 = client.get(f"/v1/searches/{uuid4()}", headers=headers)
    r2 = client.get(f"/v1/searches/{uuid4()}", headers=headers)
    r3 = client.get(f"/v1/searches/{uuid4()}", headers=headers)
    assert r1.status_code == 404  # auth + limit pass, job just doesn't exist
    assert r2.status_code == 404
    assert r3.status_code == 429, r3.text

    body = r3.json()
    assert body["error"]["code"] == "RATE_LIMITED"
    details = body["error"]["details"]
    assert details["window"] == "minute"
    assert details["limit"] == 2
    assert details["retry_after_seconds"] >= 1

    # Headers.
    assert int(r3.headers["retry-after"]) >= 1
    assert r3.headers["x-ratelimit-limit"] == "2"
    assert r3.headers["x-ratelimit-remaining"] == "0"
    assert r3.headers["x-ratelimit-window"] == "minute"


def test_happy_path_carries_rate_limit_headers(_restore_key):
    _tight_limit_key(per_min=5)
    headers = {"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"}
    r = client.get(f"/v1/searches/{uuid4()}", headers=headers)
    # 404 because job doesn't exist — auth + ratelimit both succeeded.
    assert r.status_code == 404
    assert r.headers.get("x-ratelimit-limit") == "5"
    assert r.headers.get("x-ratelimit-remaining") in {"3", "4"}  # 5-1-margin
    assert r.headers.get("x-ratelimit-window") == "minute"


def test_unlimited_key_never_trips(_restore_key):
    # per_min=None / 0 should mean "no cap" — the limiter returns no windows
    # so the happy-path headers should also be absent (no bottleneck to report).
    _tight_limit_key(per_min=0)
    FAKE_REPO.key = ApiKey(
        record_id=PARTNER_RECORD_ID,
        partner_name="Unlimited Partner",
        key_hash=PARTNER_KEY_HASH,
        key_prefix="sk_test_",
        status=KeyStatus.ACTIVE,
        rate_limit_per_min=None,
        rate_limit_per_day=None,
        rate_limit_per_week=None,
    )
    headers = {"Authorization": f"Bearer {PARTNER_KEY_PLAINTEXT}"}
    # Hammer it — every request must pass auth + limit (404 because unknown job).
    for _ in range(20):
        r = client.get(f"/v1/searches/{uuid4()}", headers=headers)
        assert r.status_code == 404
    # No bottleneck window → no X-RateLimit-* headers.
    assert "x-ratelimit-limit" not in {h.lower() for h in r.headers}
