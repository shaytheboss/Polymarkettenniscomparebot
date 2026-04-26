import logging
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os

from app.config import settings
from app.api import health, matches, opportunities, players, settings_api
from app.workers.scheduler import get_scheduler
from app.workers.jobs import job_refresh_elo

if settings.sentry_dsn:
    sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.app_env)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("Tennis Arb Bot starting up...")

    scheduler = get_scheduler()
    scheduler.start()
    logger.info("Scheduler started")

    # Run ELO refresh on startup if never done
    await job_refresh_elo()

    yield

    scheduler.shutdown(wait=False)
    logger.info("Shutting down")


app = FastAPI(
    title="Tennis Arb Bot",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["health"])
app.include_router(matches.router, prefix="/api/matches", tags=["matches"])
app.include_router(opportunities.router, prefix="/api/opportunities", tags=["opportunities"])
app.include_router(players.router, prefix="/api/players", tags=["players"])
app.include_router(settings_api.router, prefix="/api/settings", tags=["settings"])

# Serve React dashboard build in production
dashboard_dist = os.path.join(os.path.dirname(__file__), "..", "dashboard", "dist")
if os.path.isdir(dashboard_dist):
    app.mount("/", StaticFiles(directory=dashboard_dist, html=True), name="dashboard")
