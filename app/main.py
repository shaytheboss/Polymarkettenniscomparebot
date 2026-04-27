import asyncio
import logging
import os
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.api import health, matches, opportunities, players, settings_api
from app.workers.scheduler import get_scheduler
from app.workers.jobs import job_refresh_elo

if settings.sentry_dsn:
    sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.app_env)

logger = logging.getLogger(__name__)


async def _keepalive_task():
    """Ping own /health every 14 min to prevent Railway free-tier sleep."""
    import httpx
    port = int(os.environ.get("PORT", 8000))
    url = f"http://localhost:{port}/health"
    await asyncio.sleep(60)  # wait for startup
    while True:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.get(url)
        except Exception:
            pass
        await asyncio.sleep(14 * 60)


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

    # Keep Railway free tier awake
    keepalive = asyncio.create_task(_keepalive_task())

    # Start Telegram bot polling (receives /start, /live, etc.)
    tg_app = None
    if settings.telegram_bot_token:
        try:
            from app.bot.telegram_bot import get_app as get_tg_app
            tg_app = get_tg_app()
            await tg_app.initialize()
            await tg_app.start()
            await tg_app.updater.start_polling(drop_pending_updates=True)
            logger.info("Telegram bot polling started")
        except Exception as exc:
            logger.error(f"Telegram bot failed to start: {exc}", exc_info=True)
            tg_app = None

    # Run ELO refresh on startup if never done
    await job_refresh_elo()

    yield

    keepalive.cancel()

    if tg_app:
        try:
            await tg_app.updater.stop()
            await tg_app.stop()
            await tg_app.shutdown()
        except Exception:
            pass

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
