"""The grants table id is hardcoded as a baked-in default, so a missing
AIRTABLE_GRANTS_TABLE_ID env var never crashes the partner-facing API — the app
boots with the default and reverse search stays enabled.

This guards the regression behind the boot crash-loop (the id was once a required
setting that wasn't present in the Render environment, taking the whole API down).
The repo/scheduler still tolerate an explicit None as defense in depth."""

from __future__ import annotations

from app.config import Settings
from app.repositories.airtable import AirtableRepo


def test_settings_boot_without_grants_table_id(monkeypatch):
    # The env var being absent used to raise a ValidationError at import time and
    # crash boot. Now the field has a hardcoded default, so it's always present.
    monkeypatch.delenv("AIRTABLE_GRANTS_TABLE_ID", raising=False)
    settings = Settings()
    assert settings.airtable_grants_table_id == "tblnRV4RxCOv7X5u6"


def test_repo_constructs_without_grants_table():
    settings = Settings(airtable_grants_table_id=None)
    repo = AirtableRepo(settings)
    # Grants table is skipped, but the partner-API + digest tables are still wired.
    assert repo._grants is None
    assert repo._jobs is not None
    assert repo._search_matches is not None
    assert repo._companies is not None
