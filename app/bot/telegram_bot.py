"""Telegram bot entry point and alert dispatcher."""
from __future__ import annotations
import asyncio
import logging
import signal

from telegram import Bot
from telegram.ext import Application, CommandHandler

from app.config import settings
from app.bot.handlers import (
    cmd_start, cmd_help, cmd_status, cmd_live,
    cmd_opps, cmd_settings, cmd_set_edge, cmd_set_min_prob, cmd_set_tours,
    cmd_refresh, cmd_track, cmd_polytest, cmd_setpoly,
)
from app.bot.formatters import fmt_opportunity

logger = logging.getLogger(__name__)
_app: Application | None = None


def get_app() -> Application:
    global _app
    if _app is None:
        _app = Application.builder().token(settings.telegram_bot_token).build()
        _app.add_handler(CommandHandler("start",      cmd_start))
        _app.add_handler(CommandHandler("help",       cmd_help))
        _app.add_handler(CommandHandler("status",     cmd_status))
        _app.add_handler(CommandHandler("live",       cmd_live))
        _app.add_handler(CommandHandler("opps",       cmd_opps))
        _app.add_handler(CommandHandler("settings",   cmd_settings))
        _app.add_handler(CommandHandler("set_edge",     cmd_set_edge))
        _app.add_handler(CommandHandler("set_min_prob", cmd_set_min_prob))
        _app.add_handler(CommandHandler("set_tours",    cmd_set_tours))
        _app.add_handler(CommandHandler("refresh",    cmd_refresh))
        _app.add_handler(CommandHandler("track",      cmd_track))
        _app.add_handler(CommandHandler("polytest",   cmd_polytest))
        _app.add_handler(CommandHandler("setpoly",    cmd_setpoly))
    return _app


async def send_opportunity_alert(opportunity, match, db) -> None:
    """Broadcast opportunity alert to all eligible subscribers."""
    if not settings.telegram_bot_token:
        return

    from sqlalchemy import select
    from app.models.alert import TelegramUser, Alert

    p1 = match.player1
    p2 = match.player2
    p1_name = p1.name if p1 else "P1"
    p2_name = p2.name if p2 else "P2"
    p1_elo = float(match.p1_elo_at_match or (p1.current_elo if p1 else 1500) or 1500)
    p2_elo = float(match.p2_elo_at_match or (p2.current_elo if p2 else 1500) or 1500)
    elo_gap = p1_elo - p2_elo

    extra = opportunity.extra or {}

    # Use direct market URL if slug is known, otherwise generic search
    from app.utils.name_matcher import _last_name
    if getattr(match, "polymarket_slug", None):
        poly_url = f"https://polymarket.com/event/{match.polymarket_slug}"
    else:
        last1 = _last_name(p1_name).replace(" ", "+")
        last2 = _last_name(p2_name).replace(" ", "+")
        poly_url = f"https://polymarket.com/sports/tennis?q={last1}+{last2}"

    text = fmt_opportunity(
        match_name=f"{p1_name} vs {p2_name}",
        score_text=opportunity.score_text or match.score_text or "?",
        back_player=opportunity.back_player_name,
        table_prob=float(opportunity.table_prob),
        markov_prob=float(opportunity.markov_prob),
        consensus_prob=float(opportunity.consensus_prob),
        poly_price=float(opportunity.poly_price),
        edge_pp=float(opportunity.edge_pp),
        model_agreement=float(opportunity.model_agreement),
        edge_category=opportunity.edge_category or "WEAK",
        elo_band=extra.get("elo_band", "?"),
        elo_gap=elo_gap,
        surface=match.surface,
        tournament=match.tournament or "",
        tour=match.tour,
        table_notes=extra.get("table_notes", ""),
        p1_sets=opportunity.p1_sets or 0,
        p2_sets=opportunity.p2_sets or 0,
        p1_games=opportunity.p1_games or 0,
        p2_games=opportunity.p2_games or 0,
        polymarket_url=poly_url,
        last_trade_price=float(opportunity.poly_price),  # stored price is already best ask
    )

    from app.models.alert import BotSettings as _BotSettings
    min_cons_row = await db.execute(
        select(_BotSettings).where(_BotSettings.key == "min_consensus_pct")
    )
    min_cons_setting = min_cons_row.scalar_one_or_none()
    min_cons = float(min_cons_setting.value) / 100.0 if min_cons_setting else 0.0

    users_result = await db.execute(
        select(TelegramUser).where(TelegramUser.active == True)
    )
    users = users_result.scalars().all()

    bot = Bot(token=settings.telegram_bot_token)
    for user in users:
        if user.min_edge_pp and opportunity.edge_pp < user.min_edge_pp:
            continue
        if user.tours_watched and match.tour not in user.tours_watched:
            continue
        if min_cons > 0 and float(opportunity.consensus_prob) < min_cons:
            continue
        try:
            msg = await bot.send_message(
                chat_id=user.chat_id,
                text=text,
                parse_mode="MarkdownV2",
            )
            alert = Alert(
                opportunity_id=opportunity.id,
                user_id=user.id,
                message_text=text,
                telegram_message_id=msg.message_id,
                alert_type="OPPORTUNITY",
            )
            db.add(alert)
        except Exception as e:
            logger.error(f"Alert send failed to {user.chat_id}: {e}")

    opportunity.alert_sent = True
    from datetime import datetime, timezone
    opportunity.alert_sent_at = datetime.now(timezone.utc)
    await db.commit()


async def broadcast_message(text: str, parse_mode: str = None) -> None:
    """Send a plain text message to all active Telegram subscribers."""
    if not settings.telegram_bot_token:
        return

    from sqlalchemy import select
    from app.models.alert import TelegramUser
    from app.database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            users_result = await db.execute(
                select(TelegramUser).where(TelegramUser.active == True)
            )
            users = users_result.scalars().all()

        bot = Bot(token=settings.telegram_bot_token)
        for user in users:
            try:
                await bot.send_message(
                    chat_id=user.chat_id,
                    text=text,
                    parse_mode=parse_mode,
                )
            except Exception as e:
                logger.error(f"broadcast_message to {user.chat_id} failed: {e}")
    except Exception as e:
        logger.error(f"broadcast_message DB query failed: {e}")


async def main():
    if not settings.telegram_bot_token:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    app = get_app()
    stop_event = asyncio.Event()

    def _stop(sig, frame):
        stop_event.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    if settings.is_production:
        await app.initialize()
        await app.start()
        await app.updater.start_webhook(
            listen="0.0.0.0",
            port=8443,
            url_path=f"/telegram/webhook/{settings.telegram_webhook_secret}",
            secret_token=settings.telegram_webhook_secret,
        )
        await stop_event.wait()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
    else:
        async with app:
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            await stop_event.wait()
            await app.updater.stop()
            await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
