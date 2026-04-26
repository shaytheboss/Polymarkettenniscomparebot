"""APScheduler setup."""
from __future__ import annotations
import logging

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
)

logger = logging.getLogger(__name__)
_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="UTC")

        _scheduler.add_job(
            job_fetch_live_scores,
            IntervalTrigger(seconds=settings.live_scores_interval),
            id="live_scores",
            replace_existing=True,
            max_instances=1,
        )
        _scheduler.add_job(
            job_fetch_polymarket,
            IntervalTrigger(seconds=settings.polymarket_interval),
            id="polymarket",
            replace_existing=True,
            max_instances=1,
        )
        _scheduler.add_job(
            job_run_analyzer,
            IntervalTrigger(seconds=settings.analyzer_interval),
            id="analyzer",
            replace_existing=True,
            max_instances=1,
        )
        _scheduler.add_job(
            job_mark_finished,
            IntervalTrigger(seconds=60),
            id="mark_finished",
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
