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
            # Brief delay so a previous Railway instance's polling fully stops
            # before we start, avoiding 409 Conflict errors on rolling deploys.
            await asyncio.sleep(4)
            await tg_app.updater.start_polling(drop_pending_updates=True)
            logger.info("Telegram bot polling started")
        except Exception as exc:
            logger.error(f"Telegram bot failed to start: {exc}", exc_info=True)
            tg_app = None

    # Run ELO refresh on startup if never done
    await job_refresh_elo()

    # Notify subscribers that the bot is live and running automatically
    if settings.telegram_bot_token:
        try:
            from app.bot.telegram_bot import broadcast_message
            await broadcast_message(
                f"🚀 Tennis Arb Bot עלה לאוויר ורץ ברקע!\n"
                f"\n"
                f"⚙️ סריקה אוטומטית פעילה — אין צורך להפעיל כלום:\n"
                f"  • משחקים: כל {settings.live_scores_interval}שנ'\n"
                f"  • Polymarket: כל {settings.polymarket_interval}שנ'\n"
                f"  • אנליזה: כל {settings.analyzer_interval}שנ'\n"
                f"\n"
                f"ספי ברירת מחדל:\n"
                f"  edge ≥ {settings.default_min_edge_pp}pp\n"
                f"  model gap ≤ {settings.default_max_model_gap_pp}pp\n"
                f"  dedup {settings.alert_dedup_minutes}min\n"
                f"\n"
                f"📡 עדכון סטטוס יישלח כל 30 דקות."
            )
        except Exception as exc:
            logger.error(f"Startup broadcast failed: {exc}")

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
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    # Mount the assets directory so JS/CSS bundles are served directly
    _assets = os.path.join(dashboard_dist, "assets")
    if os.path.isdir(_assets):
        app.mount("/assets", StaticFiles(directory=_assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str = ""):
        # Serve exact files that live in the dist root (favicon, etc.)
        candidate = os.path.realpath(os.path.join(dashboard_dist, full_path))
        dist_root = os.path.realpath(dashboard_dist)
        if candidate.startswith(dist_root) and os.path.isfile(candidate):
            return FileResponse(candidate)
        # SPA fallback: always serve index.html for unknown paths
        index = os.path.join(dashboard_dist, "index.html")
        if os.path.isfile(index):
            return FileResponse(index)
        raise HTTPException(status_code=404)
