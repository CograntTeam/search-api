"""Shared test setup.

We set dummy env vars *before* importing the app so Pydantic's Settings
validation passes in CI where no .env file exists.
"""

import os

os.environ.setdefault("AIRTABLE_PAT", "pat_test_dummy_value_for_ci")
os.environ.setdefault("AIRTABLE_BASE_ID", "apphC0wbp5dYfACfb")
os.environ.setdefault("AIRTABLE_API_KEYS_TABLE_ID", "tblfXzQKso559HNlQ")
os.environ.setdefault("AIRTABLE_API_JOBS_TABLE_ID", "tbl5QazdvtAVbAHZO")
os.environ.setdefault("AIRTABLE_SEARCH_MATCHES_TABLE_ID", "tblLPLC7MfX7nuxyr")
os.environ.setdefault("N8N_WEBHOOK_BASE_URL", "https://example.n8n.cloud/webhook")
os.environ.setdefault("INTERNAL_SHARED_SECRET", "test_secret_value_long_enough_yes")
