"""The reverse-search config is optional: a missing AIRTABLE_GRANTS_TABLE_ID must
not crash the partner-facing API — it only disables the reverse-search poller.

This guards the regression behind the boot crash-loop (a new required setting that
wasn't present in the Render environment took the whole API down)."""

from __future__ import annotations

from app.config import Settings
from app.repositories.airtable import AirtableRepo


def test_settings_boot_without_grants_table_id(monkeypatch):
    # Simulate the env var being absent — this used to raise a ValidationError at
    # import time and crash boot. Other required vars come from conftest's env.
    monkeypatch.delenv("AIRTABLE_GRANTS_TABLE_ID", raising=False)
    settings = Settings()
    assert settings.airtable_grants_table_id is None


def test_repo_constructs_without_grants_table():
    settings = Settings(airtable_grants_table_id=None)
    repo = AirtableRepo(settings)
    # Grants table is skipped, but the partner-API + digest tables are still wired.
    assert repo._grants is None
    assert repo._jobs is not None
    assert repo._search_matches is not None
    assert repo._companies is not None
