"""APScheduler wiring for the weekly catalog sync.

Frequency comes from CATALOG_SYNC_SCHEDULE ("day_of_week hour", default "mon 6").
The job runs CatalogSyncService.sync_all_stores('scheduler') on its own DB session.
Started from the FastAPI lifespan only when the catalog API is configured.
"""

import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.config import settings
from backend.database import SessionLocal
from backend.services.catalog_sync import CatalogSyncService

log = logging.getLogger("cpq.catalog_scheduler")

_JOB_ID = "catalog_sync_weekly"


def _parse_schedule(spec: str) -> CronTrigger:
    """Parse "day_of_week hour" → CronTrigger. Falls back to Monday 6am."""
    try:
        parts = (spec or "").split()
        day_of_week = parts[0] if len(parts) >= 1 else "mon"
        hour = int(parts[1]) if len(parts) >= 2 else 6
        return CronTrigger(day_of_week=day_of_week, hour=hour)
    except (ValueError, IndexError):
        log.warning("Bad CATALOG_SYNC_SCHEDULE %r — using default 'mon 6'", spec)
        return CronTrigger(day_of_week="mon", hour=6)


async def _run_scheduled_sync() -> None:
    db = SessionLocal()
    try:
        await CatalogSyncService(db).sync_all_stores("scheduler")
    except Exception:  # noqa: BLE001 — never let the job crash the scheduler
        log.exception("Scheduled catalog sync failed")
    finally:
        db.close()


def start_catalog_scheduler() -> Optional[AsyncIOScheduler]:
    """Create + start the scheduler. Returns None if catalog API not configured."""
    if not settings.catalog_api_enabled:
        log.info("Catalog API not configured — scheduler not started")
        return None
    scheduler = AsyncIOScheduler()
    trigger = _parse_schedule(settings.CATALOG_SYNC_SCHEDULE)
    scheduler.add_job(_run_scheduled_sync, trigger, id=_JOB_ID, replace_existing=True)
    scheduler.start()
    log.info("Catalog sync scheduler started (%s)", settings.CATALOG_SYNC_SCHEDULE)
    return scheduler
