"""Shared test setup.

We set dummy env vars *before* importing the app so Pydantic's Settings
validation passes in CI where no .env file exists.
"""

import os

import pytest

os.environ.setdefault("AIRTABLE_PAT", "pat_test_dummy_value_for_ci")
os.environ.setdefault("AIRTABLE_BASE_ID", "apphC0wbp5dYfACfb")
os.environ.setdefault("AIRTABLE_API_KEYS_TABLE_ID", "tblfXzQKso559HNlQ")
os.environ.setdefault("AIRTABLE_API_JOBS_TABLE_ID", "tbl5QazdvtAVbAHZO")
os.environ.setdefault("AIRTABLE_SEARCH_MATCHES_TABLE_ID", "tblLPLC7MfX7nuxyr")
os.environ.setdefault("AIRTABLE_COMPANIES_TABLE_ID", "tblelf4Z6wnpDKr5K")
os.environ.setdefault("N8N_WEBHOOK_BASE_URL", "https://example.n8n.cloud/webhook")
os.environ.setdefault("INTERNAL_SHARED_SECRET", "test_secret_value_long_enough_yes")


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Every test starts with an empty rate-limit state.

    The limiter is a module-level singleton (one per worker in production);
    tests share the process, so without this fixture a test hammering the
    API would spill into later tests' quotas.
    """
    from app.ratelimit import rate_limiter

    rate_limiter._reset()
    yield
    rate_limiter._reset()
