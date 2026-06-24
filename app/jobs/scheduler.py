"""In-process APScheduler — the reverse-search poller and the daily digest.

Started from the FastAPI lifespan on a single Render worker, so ``max_instances=1``
+ ``coalesce=True`` are enough to prevent overlapping runs (no distributed lock
needed). APScheduler is imported lazily inside :func:`start_scheduler` so the
package is only required where the scheduler actually runs.

Each job builds its own request-scoped :class:`AirtableRepo` (pyairtable Tables
are cheap) and swallows exceptions so a single failure never kills the loop.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import Settings, get_settings
from app.repositories.airtable import AirtableRepo
from app.services.gemini_client import GeminiClient
from app.services.notifications import NotificationService
from app.services.reverse_search import ReverseSearchService
from app.services.smtp_client import SmtpClient

logger = logging.getLogger(__name__)

_scheduler: Any = None


async def _run_reverse_search() -> None:
    settings = get_settings()
    service = ReverseSearchService(AirtableRepo(settings), GeminiClient(settings), settings)
    try:
        await service.run_once()
    except Exception:  # noqa: BLE001 — keep the scheduler alive
        logger.exception("scheduler.reverse_search_failed")


async def _run_daily_digest() -> None:
    settings = get_settings()
    service = NotificationService(AirtableRepo(settings), SmtpClient(settings), settings)
    try:
        await service.run_daily()
    except Exception:  # noqa: BLE001 — keep the scheduler alive
        logger.exception("scheduler.daily_digest_failed")


async def recover_orphans_on_startup(settings: Settings) -> None:
    """Re-queue grants stranded in 'In Progress' by a killed run, once at boot.

    No-op when the reverse search isn't configured. Guarded so a transient Airtable
    error at boot never crashes the app (mirrors :func:`_run_reverse_search`).
    """
    if not settings.airtable_grants_table_id:
        return
    service = ReverseSearchService(AirtableRepo(settings), GeminiClient(settings), settings)
    try:
        await service.requeue_orphans()
    except Exception:  # noqa: BLE001 — recovery must never crash boot
        logger.exception("scheduler.orphan_recovery_failed")


def start_scheduler(settings: Settings) -> Any:
    """Build, start and stash the AsyncIOScheduler. Returns the scheduler."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler = AsyncIOScheduler(timezone=settings.scheduler_timezone)

    # The reverse-search poller is the only consumer of the grants table. When it
    # isn't configured, skip the job (loudly) instead of failing — the daily digest
    # and the partner-facing API are unaffected.
    reverse_search_enabled = bool(settings.airtable_grants_table_id)
    if reverse_search_enabled:
        scheduler.add_job(
            _run_reverse_search,
            IntervalTrigger(seconds=settings.reverse_search_poll_seconds),
            id="reverse_search_poll",
            max_instances=1,
            coalesce=True,
        )
    else:
        logger.warning(
            "scheduler.reverse_search_disabled reason=airtable_grants_table_id_missing"
        )

    scheduler.add_job(
        _run_daily_digest,
        CronTrigger(
            hour=settings.daily_email_hour,
            minute=settings.daily_email_minute,
            timezone=settings.scheduler_timezone,
        ),
        id="daily_digest",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "scheduler.started reverse_search=%s poll_seconds=%s daily=%02d:%02d tz=%s",
        reverse_search_enabled,
        settings.reverse_search_poll_seconds,
        settings.daily_email_hour,
        settings.daily_email_minute,
        settings.scheduler_timezone,
    )
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("scheduler.stopped")
