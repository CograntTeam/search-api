"""The scheduler registers the reverse-search poller only when the grants table is
configured; the daily digest is always registered. A missing grants table must
disable the poller (loudly) rather than fail."""

from __future__ import annotations

from app.config import Settings
from app.jobs.scheduler import shutdown_scheduler, start_scheduler


async def test_reverse_search_job_skipped_without_grants_table():
    shutdown_scheduler()  # ensure a clean module-level singleton
    scheduler = start_scheduler(Settings(airtable_grants_table_id=None))
    try:
        job_ids = {job.id for job in scheduler.get_jobs()}
    finally:
        shutdown_scheduler()
    assert "reverse_search_poll" not in job_ids
    assert "daily_digest" in job_ids


async def test_reverse_search_job_registered_with_grants_table():
    shutdown_scheduler()
    scheduler = start_scheduler(Settings())  # conftest env provides the grants id
    try:
        job_ids = {job.id for job in scheduler.get_jobs()}
    finally:
        shutdown_scheduler()
    assert "reverse_search_poll" in job_ids
    assert "daily_digest" in job_ids
