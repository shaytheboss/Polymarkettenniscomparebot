"""Telegram bot command handlers."""
from __future__ import annotations
import logging
from datetime import date, datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy import select, func

from app.database import AsyncSessionLocal
from app.models.alert import TelegramUser, BotSettings
from app.models.match import Match
from app.models.opportunity import Opportunity
from app.bot.formatters import fmt_status, fmt_match_prob

logger = logging.getLogger(__name__)


async def _get_or_create_user(chat_id: int, username: str, db) -> TelegramUser:
    result = await db.execute(select(TelegramUser).where(TelegramUser.chat_id == chat_id))
    user = result.scalar_one_or_none()
    if user is None:
        user = TelegramUser(chat_id=chat_id, username=username)
        db.add(user)
        await db.commit()
    return user


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    username = update.effective_user.username or ""
    async with AsyncSessionLocal() as db:
        await _get_or_create_user(chat_id, username, db)
    await update.message.reply_text(
        "🎾 *Tennis Arb Bot* started!\n\n"
        "I monitor live ATP/WTA matches on Polymarket and alert you when "
        "our Markov + empirical models show an edge.\n\n"
        "Commands:\n"
        "/status — bot status\n"
        "/live — current live matches with probabilities\n"
        "/opps — today's opportunities\n"
        "/settings — view/change your alert settings\n"
        "/set\\_edge 8 — set minimum edge in pp (default 5)\n"
        "/set\\_tours ATP — filter tours (ATP/WTA/ALL)\n",
        parse_mode="Markdown",
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    async with AsyncSessionLocal() as db:
        live_count_result = await db.execute(
            select(func.count()).select_from(Match).where(Match.status == "live")
        )
        live_count = live_count_result.scalar() or 0

        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        opp_count_result = await db.execute(
            select(func.count()).select_from(Opportunity).where(
                Opportunity.detected_at >= today_start
            )
        )
        opp_count = opp_count_result.scalar() or 0

        elo_result = await db.execute(
            select(BotSettings).where(BotSettings.key == "last_elo_refresh")
        )
        elo_setting = elo_result.scalar_one_or_none()
        elo_ts = elo_setting.value if elo_setting else None

    text = fmt_status(
        live_matches=live_count,
        opportunities_today=opp_count,
        last_elo_refresh=elo_ts,
        bot_running=True,
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_live(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Match).where(Match.status == "live").order_by(Match.updated_at.desc()).limit(10)
        )
        matches = result.scalars().all()

    if not matches:
        await update.message.reply_text("No live matches tracked right now.")
        return

    for match in matches[:5]:
        p1_name = match.player1.name if match.player1 else "P1"
        p2_name = match.player2.name if match.player2 else "P2"
        match_name = f"{p1_name} vs {p2_name}"

        # Latest snapshot
        last_snap = match.snapshots[-1] if match.snapshots else None
        if last_snap:
            text = fmt_match_prob(
                match_name=match_name,
                score_text=match.score_text or "0-0",
                table_prob=last_snap.table_prob_p1 or 0.5,
                markov_prob=last_snap.markov_prob_p1 or 0.5,
                consensus_prob=last_snap.consensus_prob_p1 or 0.5,
                poly_price=last_snap.poly_price_p1,
                elo_band=last_snap.raw_data.get("elo_band", "?") if last_snap.raw_data else "?",
                surface=match.surface,
            )
            await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_opps(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    async with AsyncSessionLocal() as db:
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        result = await db.execute(
            select(Opportunity)
            .where(Opportunity.detected_at >= today_start)
            .order_by(Opportunity.edge_pp.desc())
            .limit(10)
        )
        opps = result.scalars().all()

    if not opps:
        await update.message.reply_text("No opportunities detected today yet.")
        return

    lines = ["*Today's Opportunities*\n"]
    for opp in opps:
        cat_emoji = {"STRONG": "🔴", "MODERATE": "🟡", "WEAK": "🟢"}.get(opp.edge_category, "⚪")
        lines.append(
            f"{cat_emoji} {opp.back_player_name} | "
            f"edge={opp.edge_pp:.1f}pp | "
            f"cons={opp.consensus_prob*100:.0f}% vs poly={opp.poly_price*100:.0f}% | "
            f"{opp.score_text or 'N/A'}"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    async with AsyncSessionLocal() as db:
        user = await _get_or_create_user(chat_id, "", db)
    await update.message.reply_text(
        f"*Your Settings*\n\n"
        f"Min edge: `{user.min_edge_pp}pp`\n"
        f"Max model gap: `{user.min_model_agreement}pp`\n"
        f"Tours: `{', '.join(user.tours_watched) if user.tours_watched else 'ALL'}`\n\n"
        f"Use /set\\_edge N or /set\\_tours ATP|WTA|ALL to change",
        parse_mode="Markdown",
    )


async def cmd_set_edge(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    try:
        val = int(ctx.args[0])
        if not 1 <= val <= 40:
            raise ValueError
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /set_edge 8 (integer 1-40)")
        return
    async with AsyncSessionLocal() as db:
        user = await _get_or_create_user(chat_id, "", db)
        user.min_edge_pp = val
        await db.commit()
    await update.message.reply_text(f"✅ Min edge set to {val}pp")


async def cmd_set_tours(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    try:
        val = ctx.args[0].upper()
        if val == "ALL":
            tours = None
        elif val in ("ATP", "WTA"):
            tours = [val]
        else:
            raise ValueError
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /set_tours ATP | WTA | ALL")
        return
    async with AsyncSessionLocal() as db:
        user = await _get_or_create_user(chat_id, "", db)
        user.tours_watched = tours
        await db.commit()
    await update.message.reply_text(f"✅ Tours set to {val}")
