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
from app.config import settings
from app.bot.formatters import (
    fmt_status, fmt_match_prob, fmt_opps_list, fmt_help
)

logger = logging.getLogger(__name__)

_DEFAULTS = {
    "min_edge_pp":      settings.default_min_edge_pp,
    "max_model_gap_pp": settings.default_max_model_gap_pp,
    "alert_dedup_min":  settings.alert_dedup_minutes,
}


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
        min_cons_result = await db.execute(
            select(BotSettings).where(BotSettings.key == "min_consensus_pct")
        )
        min_cons_setting = min_cons_result.scalar_one_or_none()
        min_cons_pct = int(min_cons_setting.value) if min_cons_setting else 0

    tours_str = ", ".join(user.tours_watched) if user.tours_watched else "הכל"
    edge_marker = "" if user.min_edge_pp == _DEFAULTS["min_edge_pp"] else " ✏️"
    cons_label = f"{min_cons_pct}%\\+" if min_cons_pct > 0 else "ללא סף"
    await update.message.reply_text(
        f"*הגדרות שלך*\n\n"
        f"פער מינימלי \\(edge\\): `{user.min_edge_pp}pp`{edge_marker}\n"
        f"הסתברות מינימלית שלנו: `{cons_label}`\n"
        f"טורים: `{tours_str}`\n\n"
        f"*ברירות מחדל:*\n"
        f"  edge ≥ `{_DEFAULTS['min_edge_pp']}pp`"
        f" \\| model gap ≤ `{_DEFAULTS['max_model_gap_pp']}pp`\n\n"
        f"פקודות שינוי:\n"
        f"/set\\_edge 8 — פער מינימלי מול Polymarket\n"
        f"/set\\_min\\_prob 70 — הסתברות מינימלית שלנו \\(0 = הכל\\)\n"
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


async def cmd_set_min_prob(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Set minimum consensus probability threshold (0 = no filter)."""
    try:
        val = int(ctx.args[0])
        if not 0 <= val <= 99:
            raise ValueError
    except (IndexError, ValueError):
        await update.message.reply_text(
            "שימוש: /set\\_min\\_prob 70\n0 = ללא סף \\| 70 = רק כשאנחנו ≥70%",
            parse_mode="MarkdownV2",
        )
        return
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(BotSettings).where(BotSettings.key == "min_consensus_pct"))
        setting = result.scalar_one_or_none()
        if setting:
            setting.value = str(val)
        else:
            db.add(BotSettings(key="min_consensus_pct", value=str(val)))
        await db.commit()
    label = f"{val}%\\+" if val > 0 else "ללא סף"
    await update.message.reply_text(
        f"✅ הסתברות מינימלית שלנו: `{label}`", parse_mode="MarkdownV2"
    )


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


async def cmd_refresh(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Force-fetch all sources now and show full diagnostic output."""
    from app.collectors.tennis_live import fetch_all_today
    await update.message.reply_text(
        f"מריץ סריקה מלאה (Sofascore → ESPN → TheSportsDB)...\n"
        f"ברירות מחדל: edge≥{_DEFAULTS['min_edge_pp']}pp | "
        f"model_gap≤{_DEFAULTS['max_model_gap_pp']}pp | "
        f"dedup={_DEFAULTS['alert_dedup_min']}min"
    )
    try:
        all_matches = await fetch_all_today()
    except Exception as exc:
        await update.message.reply_text(f"שגיאה: {exc}")
        return

    live      = [m for m in all_matches if m["status"] == "live"]
    scheduled = [m for m in all_matches if m["status"] == "scheduled"]
    finished  = [m for m in all_matches if m["status"] == "finished"]

    if not all_matches:
        await update.message.reply_text(
            "⚠️ כל 3 המקורות החזירו 0 משחקים היום.\n"
            "• Sofascore: 403 (Cloudflare חוסם IPs של Railway)\n"
            "• ESPN: מחזיר מיכלי טורניר בלי משחקים בודדים\n"
            "• TheSportsDB: גם כן ריק\n"
            "בדוק שיש משחקים בפועל ב-tennisabstract.com"
        )
        return

    lines = [
        f"✅ סריקה הצליחה: {len(all_matches)} משחקים",
        f"🟢 חיים: {len(live)}  🕐 מתוכננים: {len(scheduled)}  ✅ סיום: {len(finished)}",
        "",
    ]

    # Show all matches, grouped by status
    shown = 0
    for m in (live + scheduled + finished):
        if shown >= 12:
            lines.append(f"... ועוד {len(all_matches) - shown} משחקים")
            break
        icon = {"live": "🟢", "scheduled": "🕐", "finished": "✅"}.get(m["status"], "❓")
        score = m.get("score_text") or "לא התחיל"
        src = m["external_id"].split("_")[0].upper()
        lines.append(
            f"{icon} [{src}] {m['player1_name']} vs {m['player2_name']} "
            f"({m['tour']} | {m['surface']})"
        )
        if score and score != "לא התחיל":
            lines.append(f"   ניקוד: {score}")
        shown += 1

    # DB state
    async with AsyncSessionLocal() as db:
        db_live = (await db.execute(
            select(func.count()).select_from(Match).where(Match.status == "live")
        )).scalar() or 0
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        db_total = (await db.execute(
            select(func.count()).select_from(Match)
            .where(Match.updated_at >= today_start)
        )).scalar() or 0
        db_opps = (await db.execute(
            select(func.count()).select_from(Opportunity)
            .where(Opportunity.detected_at >= today_start)
        )).scalar() or 0

    lines += [
        "",
        f"📊 DB: {db_live} חיים | {db_total} עודכנו היום | {db_opps} הזדמנויות",
    ]
    await update.message.reply_text("\n".join(lines))


async def cmd_track(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Search ESPN for a player and show their current match details."""
    query = " ".join(ctx.args).strip() if ctx.args else ""
    if not query:
        await update.message.reply_text(
            "שימוש: /track שם\\_שחקן\nדוגמה: /track Alcaraz",
            parse_mode="MarkdownV2",
        )
        return

    from app.collectors.tennis_live import fetch_all_today
    all_matches = await fetch_all_today()

    ql = query.lower()
    found = [
        m for m in all_matches
        if ql in m["player1_name"].lower() or ql in m["player2_name"].lower()
    ]

    if not found:
        await update.message.reply_text(
            f"לא נמצא '{query}' ב-ESPN.\n"
            f"ESPN מציג {len(all_matches)} אירועים היום."
        )
        return

    for m in found[:3]:
        icon = {"live": "🟢", "finished": "✅", "scheduled": "🕐"}.get(m["status"], "❓")
        score = m.get("score_text") or "לא התחיל"
        await update.message.reply_text(
            f"{icon} {m['player1_name']} vs {m['player2_name']}\n"
            f"טור: {m['tour']} | מגרש: {m['surface']}\n"
            f"ניקוד: {score}\n"
            f"סטטוס: {m['status']}"
        )


async def cmd_polytest(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Test Polymarket API connectivity and show diagnostic information."""
    await update.message.reply_text("🔍 בודק חיבור ל-Polymarket...")

    from app.collectors.polymarket import test_connectivity
    diag = await test_connectivity()

    if diag.get("relay_configured"):
        route_line = f"🔀 Relay: {diag['relay_hint']}"
    elif diag.get("proxy_configured"):
        route_line = f"🔀 Proxy: {diag['proxy_hint']}"
    else:
        route_line = "⚠️ אין Relay — Railway IPs חסומים ע\"י Polymarket"

    endpoint_line = f"🌐 Endpoint: {diag.get('endpoint', '?')}"

    if diag["ok"]:
        status_line = f"✅ API זמין — {diag['markets_found']} שווקי טניס"
        sample_line = f"דוגמה: \"{diag['sample_question']}\"" if diag["sample_question"] else ""
    else:
        status_line = f"❌ API חסום — {diag['http_status']}"
        sample_line = (
            "\nפתרון חינמי (Cloudflare Worker):\n"
            "1. cloudflare.com → Workers → Create Worker\n"
            "2. הדבק את קוד cloudflare-worker.js מה-repo\n"
            "3. Save & Deploy → קבל URL\n"
            "4. Railway Variables: POLYMARKET_RELAY_URL=<url>\n"
            "\nחלופה: /setpoly <שחקן> <condition_id>"
        )

    lines = [route_line, endpoint_line, status_line]
    if sample_line:
        lines.append(sample_line)

    if diag["last_cache_error"]:
        lines.append(f"שגיאה אחרונה: {diag['last_cache_error'][:80]}")

    # Also show current live matches with Polymarket linkage status
    async with AsyncSessionLocal() as db:
        from sqlalchemy.orm import selectinload
        result = await db.execute(
            select(Match)
            .options(selectinload(Match.player1), selectinload(Match.player2))
            .where(Match.status == "live")
            .limit(5)
        )
        live = result.scalars().all()

    if live:
        lines.append("\nמשחקים חיים:")
        for m in live:
            p1 = m.player1.name if m.player1 else "?"
            p2 = m.player2.name if m.player2 else "?"
            poly_status = (
                f"✅ {m.last_poly_price_p1*100:.0f}%"
                if m.last_poly_price_p1 is not None
                else ("🔗 מקושר" if m.polymarket_condition_id else "❌ לא נמצא")
            )
            lines.append(f"  {p1} vs {p2} → {poly_status}")

    await update.message.reply_text("\n".join(lines))


async def cmd_setpoly(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Manually link a Polymarket market to a live match.
    Usage: /setpoly <player_name> <condition_id_or_slug>

    The condition ID or slug comes from the Polymarket market URL:
      https://polymarket.com/event/{slug}?tid={condition_id}

    Examples:
      /setpoly Alcaraz 0xabc123...
      /setpoly Djokovic will-djokovic-beat-alcaraz
    """
    args = ctx.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "שימוש: /setpoly <שחקן> <condition_id>\n\n"
            "מצא את ה-condition_id בכתובת URL של השוק ב-Polymarket:\n"
            "https://polymarket.com/event/{slug}?tid={condition_id}\n\n"
            "דוגמה:\n"
            "/setpoly Alcaraz 0x1234abcd..."
        )
        return

    player_query = args[0].lower()
    condition_id = args[1]

    async with AsyncSessionLocal() as db:
        from sqlalchemy.orm import selectinload
        result = await db.execute(
            select(Match)
            .options(selectinload(Match.player1), selectinload(Match.player2))
            .where(Match.status == "live")
        )
        live = result.scalars().all()

    # Find matching live match
    match = None
    for m in live:
        p1 = m.player1.name if m.player1 else ""
        p2 = m.player2.name if m.player2 else ""
        if player_query in p1.lower() or player_query in p2.lower():
            match = m
            break

    if not match:
        names = [
            f"{m.player1.name if m.player1 else '?'} vs {m.player2.name if m.player2 else '?'}"
            for m in live
        ]
        await update.message.reply_text(
            f"לא נמצא משחק חי עם '{args[0]}'.\n"
            f"משחקים חיים:\n" + "\n".join(f"  • {n}" for n in names) if names else "אין משחקים חיים."
        )
        return

    # Update the match
    async with AsyncSessionLocal() as db:
        from sqlalchemy.orm import selectinload
        result = await db.execute(
            select(Match)
            .options(selectinload(Match.player1), selectinload(Match.player2))
            .where(Match.id == match.id)
        )
        m = result.scalar_one()
        m.polymarket_condition_id = condition_id
        # If it looks like a slug (not hex), also store as slug
        if not condition_id.startswith("0x"):
            m.polymarket_slug = condition_id

        # Try to immediately fetch the price
        from app.collectors.polymarket import fetch_market_price
        price = await fetch_market_price(condition_id)
        if price is not None:
            from datetime import datetime, timezone
            m.last_poly_price_p1 = price
            m.poly_updated_at = datetime.now(timezone.utc)

        await db.commit()

    p1 = match.player1.name if match.player1 else "?"
    p2 = match.player2.name if match.player2 else "?"

    if price is not None:
        await update.message.reply_text(
            f"✅ {p1} vs {p2}\n"
            f"Polymarket מקושר: {condition_id[:20]}...\n"
            f"מחיר: {p1.split()[-1]} {price*100:.0f}% | {p2.split()[-1]} {(1-price)*100:.0f}%"
        )
    else:
        await update.message.reply_text(
            f"🔗 {p1} vs {p2} — condition ID נשמר: {condition_id[:20]}...\n"
            f"⚠️ לא הצלחתי לקרוא מחיר. ודא שה-ID נכון ושה-API זמין."
        )
