"""APScheduler setup."""
from __future__ import annotations
import logging

from apscheduler.events import EVENT_JOB_ERROR
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.workers.jobs import (
    job_fetch_live_scores,
    job_run_analyzer,
    job_fetch_polymarket,
    job_refresh_elo,
    job_mark_finished,
    job_heartbeat,
)

logger = logging.getLogger(__name__)
_scheduler: AsyncIOScheduler | None = None


def _on_job_error(event):
    logger.error(
        f"Scheduler job {event.job_id} raised an exception: {event.exception}",
        exc_info=event.traceback,
    )


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="UTC")
        _scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)

        _scheduler.add_job(
            job_fetch_live_scores,
            IntervalTrigger(seconds=settings.live_scores_interval),
            id="live_scores",
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=10,
        )
        _scheduler.add_job(
            job_fetch_polymarket,
            IntervalTrigger(seconds=settings.polymarket_interval),
            id="polymarket",
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=10,
        )
        _scheduler.add_job(
            job_run_analyzer,
            IntervalTrigger(seconds=settings.analyzer_interval),
            id="analyzer",
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=15,
        )
        _scheduler.add_job(
            job_mark_finished,
            IntervalTrigger(seconds=60),
            id="mark_finished",
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=30,
        )
        # Heartbeat every 5 minutes
        _scheduler.add_job(
            job_heartbeat,
            IntervalTrigger(minutes=5),
            id="heartbeat",
            replace_existing=True,
            max_instances=1,
        )
        # ELO refresh once per day at 03:00 UTC
        _scheduler.add_job(
            job_refresh_elo,
            CronTrigger(hour=3, minute=0),
            id="elo_refresh",
            replace_existing=True,
            max_instances=1,
        )

    return _scheduler
