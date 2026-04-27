"""Telegram bot command handlers."""
from __future__ import annotations
import logging
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy import select, func

from app.database import AsyncSessionLocal
from app.models.alert import TelegramUser, BotSettings
from app.models.match import Match, MatchSnapshot
from app.models.opportunity import Opportunity
from app.models.player import Player
from app.bot.formatters import (
    fmt_status, fmt_match_prob, fmt_opps_list, fmt_help
)

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
        user = await _get_or_create_user(chat_id, username, db)
        is_new = user.created_at and (datetime.now(timezone.utc) - user.created_at).total_seconds() < 5
    greeting = "ברוך הבא" if is_new else "ברוך השב"
    await update.message.reply_text(
        fmt_help().replace("*Tennis Arb Bot — עזרה*", f"*{greeting} ל\\-Tennis Arb Bot\\!*"),
        parse_mode="MarkdownV2",
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(fmt_help(), parse_mode="MarkdownV2")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    async with AsyncSessionLocal() as db:
        live_count = (await db.execute(
            select(func.count()).select_from(Match).where(Match.status == "live")
        )).scalar() or 0

        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

        opp_count = (await db.execute(
            select(func.count()).select_from(Opportunity)
            .where(Opportunity.detected_at >= today_start)
        )).scalar() or 0

        strong_count = (await db.execute(
            select(func.count()).select_from(Opportunity)
            .where(Opportunity.detected_at >= today_start, Opportunity.edge_category == "STRONG")
        )).scalar() or 0

        atp_players = (await db.execute(
            select(func.count()).select_from(Player).where(Player.tour == "ATP")
        )).scalar() or 0

        wta_players = (await db.execute(
            select(func.count()).select_from(Player).where(Player.tour == "WTA")
        )).scalar() or 0

        elo_result = await db.execute(
            select(BotSettings).where(BotSettings.key == "last_elo_refresh")
        )
        elo_setting = elo_result.scalar_one_or_none()

    await update.message.reply_text(
        fmt_status(
            live_matches=live_count,
            opportunities_today=opp_count,
            strong_today=strong_count,
            last_elo_refresh=elo_setting.value if elo_setting else None,
            bot_running=True,
            atp_players=atp_players,
            wta_players=wta_players,
        ),
        parse_mode="MarkdownV2",
    )


async def cmd_live(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Match).where(Match.status == "live")
            .order_by(Match.updated_at.desc()).limit(8)
        )
        matches = result.scalars().all()

    if not matches:
        await update.message.reply_text("אין משחקים חיים כרגע\\.", parse_mode="MarkdownV2")
        return

    for match in matches:
        p1 = match.player1
        p2 = match.player2
        if not p1 or not p2:
            continue

        last_snap = match.snapshots[-1] if match.snapshots else None
        if not last_snap:
            continue

        text = fmt_match_prob(
            match_name=f"{p1.name} vs {p2.name}",
            score_text=match.score_text or "0-0",
            p1_name=p1.name,
            p2_name=p2.name,
            p1_elo=match.p1_elo_at_match or p1.current_elo or 1500,
            p2_elo=match.p2_elo_at_match or p2.current_elo or 1500,
            table_prob_p1=last_snap.table_prob_p1 or 0.5,
            markov_prob_p1=last_snap.markov_prob_p1 or 0.5,
            consensus_prob_p1=last_snap.consensus_prob_p1 or 0.5,
            poly_price_p1=last_snap.poly_price_p1,
            elo_band=last_snap.raw_data.get("elo_band", "?") if last_snap.raw_data else "?",
            surface=match.surface,
            tour=match.tour,
            table_notes=last_snap.raw_data.get("table_notes", "") if last_snap.raw_data else "",
        )
        try:
            await update.message.reply_text(text, parse_mode="MarkdownV2")
        except Exception as e:
            logger.error(f"Failed to send live match message: {e}")


async def cmd_opps(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    async with AsyncSessionLocal() as db:
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        result = await db.execute(
            select(Opportunity)
            .where(Opportunity.detected_at >= today_start)
            .order_by(Opportunity.edge_pp.desc())
            .limit(12)
        )
        opps = result.scalars().all()

    opps_dicts = [
        {
            "back_player_name": o.back_player_name,
            "score_text": o.score_text,
            "edge_pp": o.edge_pp,
            "edge_category": o.edge_category,
            "consensus_prob": o.consensus_prob,
            "poly_price": o.poly_price,
            "outcome": o.outcome,
        }
        for o in opps
    ]
    await update.message.reply_text(
        fmt_opps_list(opps_dicts),
        parse_mode="MarkdownV2",
    )


async def cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    async with AsyncSessionLocal() as db:
        user = await _get_or_create_user(chat_id, "", db)

    tours_str = ", ".join(user.tours_watched) if user.tours_watched else "הכל"
    await update.message.reply_text(
        f"*הגדרות שלך*\n\n"
        f"פער מינימלי להתראה: `{user.min_edge_pp}pp`\n"
        f"פער מקסימלי בין מודלים: `{user.min_model_agreement}pp`\n"
        f"טורים: `{tours_str}`\n\n"
        f"שינוי:\n"
        f"/set\\_edge 8 — פער מינימלי\n"
        f"/set\\_tours ATP — ATP / WTA / ALL",
        parse_mode="MarkdownV2",
    )


async def cmd_set_edge(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    try:
        val = int(ctx.args[0])
        if not 1 <= val <= 50:
            raise ValueError
    except (IndexError, ValueError):
        await update.message.reply_text("שימוש: /set\\_edge 8 \\(מספר שלם 1\\-50\\)", parse_mode="MarkdownV2")
        return
    async with AsyncSessionLocal() as db:
        user = await _get_or_create_user(chat_id, "", db)
        user.min_edge_pp = val
        await db.commit()
    await update.message.reply_text(f"✅ פער מינימלי הוגדר ל\\-`{val}pp`", parse_mode="MarkdownV2")


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
        await update.message.reply_text("שימוש: /set\\_tours ATP \\| WTA \\| ALL", parse_mode="MarkdownV2")
        return
    async with AsyncSessionLocal() as db:
        user = await _get_or_create_user(chat_id, "", db)
        user.tours_watched = tours
        await db.commit()
    label = val if val != "ALL" else "הכל"
    await update.message.reply_text(f"✅ טורים: `{label}`", parse_mode="MarkdownV2")
