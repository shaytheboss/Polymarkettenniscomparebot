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
        try:
            ts = user.created_at
            if ts is not None and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            is_new = ts is not None and (datetime.now(timezone.utc) - ts).total_seconds() < 10
        except Exception:
            is_new = False
    greeting = "Welcome to" if is_new else "Welcome back to"
    try:
        await update.message.reply_text(
            fmt_help().replace("*Tennis Arb Bot — Help*", f"*{greeting} Tennis Arb Bot\\!*"),
            parse_mode="MarkdownV2",
        )
    except Exception as e:
        logger.error(f"cmd_start MarkdownV2 failed: {e}")
        await update.message.reply_text(
            f"{greeting} Tennis Arb Bot!\n\nCommands: /status /live /opps /settings /help"
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
        await update.message.reply_text("No live matches right now\\.", parse_mode="MarkdownV2")
        return

    from app.collectors.polymarket import fetch_market_meta

    for match in matches:
        p1 = match.player1
        p2 = match.player2
        if not p1 or not p2:
            continue

        last_snap = match.snapshots[-1] if match.snapshots else None
        if not last_snap:
            continue

        # Fetch live Polymarket metadata (URL/volume/last-trade) if we have a link.
        meta = {}
        if match.polymarket_condition_id or match.polymarket_slug:
            try:
                meta = await fetch_market_meta(
                    condition_id=match.polymarket_condition_id,
                    slug=match.polymarket_slug,
                )
            except Exception as e:
                logger.warning(f"polymarket meta fetch failed for match {match.id}: {e}")
                meta = {}

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
            last_trade_p1=meta.get("last_trade_p1"),
            last_trade_p2=meta.get("last_trade_p2"),
            poly_slug=match.polymarket_slug or meta.get("slug"),
            poly_condition_id=match.polymarket_condition_id or meta.get("condition_id"),
            poly_volume_24h=meta.get("volume_24h"),
        )
        try:
            await update.message.reply_text(text, parse_mode="MarkdownV2", disable_web_page_preview=True)
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

    tours_str = ", ".join(user.tours_watched) if user.tours_watched else "ALL"
    edge_marker = "" if user.min_edge_pp == _DEFAULTS["min_edge_pp"] else " ✏️"
    cons_label = f"{min_cons_pct}%\\+" if min_cons_pct > 0 else "no threshold"
    await update.message.reply_text(
        f"*Your Settings*\n\n"
        f"Min edge: `{user.min_edge_pp}pp`{edge_marker}\n"
        f"Min consensus prob: `{cons_label}`\n"
        f"Tours: `{tours_str}`\n\n"
        f"*Defaults:*\n"
        f"  edge ≥ `{_DEFAULTS['min_edge_pp']}pp`"
        f" \\| model gap ≤ `{_DEFAULTS['max_model_gap_pp']}pp`\n\n"
        f"Change settings:\n"
        f"/set\\_edge 8 — min edge vs Polymarket\n"
        f"/set\\_min\\_prob 70 — min consensus probability \\(0 = all\\)\n"
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
        await update.message.reply_text(
            "Usage: /set\\_edge 8 \\(integer 1\\-50\\)", parse_mode="MarkdownV2"
        )
        return
    async with AsyncSessionLocal() as db:
        user = await _get_or_create_user(chat_id, "", db)
        user.min_edge_pp = val
        await db.commit()
    await update.message.reply_text(f"✅ Min edge set to `{val}pp`", parse_mode="MarkdownV2")


async def cmd_set_min_prob(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Set minimum consensus probability threshold (0 = no filter)."""
    try:
        val = int(ctx.args[0])
        if not 0 <= val <= 99:
            raise ValueError
    except (IndexError, ValueError):
        await update.message.reply_text(
            "Usage: /set\\_min\\_prob 70\n0 = no threshold \\| 70 = only when model ≥70%",
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
    label = f"{val}%\\+" if val > 0 else "no threshold"
    await update.message.reply_text(
        f"✅ Min consensus probability: `{label}`", parse_mode="MarkdownV2"
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
        await update.message.reply_text(
            "Usage: /set\\_tours ATP \\| WTA \\| ALL", parse_mode="MarkdownV2"
        )
        return
    async with AsyncSessionLocal() as db:
        user = await _get_or_create_user(chat_id, "", db)
        user.tours_watched = tours
        await db.commit()
    label = val if val != "ALL" else "ALL"
    await update.message.reply_text(f"✅ Tours: `{label}`", parse_mode="MarkdownV2")


async def cmd_refresh(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Force-fetch all sources now and show full diagnostic output."""
    from app.collectors.tennis_live import fetch_all_today
    await update.message.reply_text(
        f"Running full scan (Sofascore -> ESPN -> TheSportsDB)...\n"
        f"Defaults: edge>={_DEFAULTS['min_edge_pp']}pp | "
        f"model_gap<={_DEFAULTS['max_model_gap_pp']}pp | "
        f"dedup={_DEFAULTS['alert_dedup_min']}min"
    )
    try:
        all_matches = await fetch_all_today()
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")
        return

    live      = [m for m in all_matches if m["status"] == "live"]
    scheduled = [m for m in all_matches if m["status"] == "scheduled"]
    finished  = [m for m in all_matches if m["status"] == "finished"]

    if not all_matches:
        await update.message.reply_text(
            "All 3 sources returned 0 matches today.\n"
            "- Sofascore: 403 (Cloudflare blocks Railway IPs)\n"
            "- ESPN: returns tournament containers without individual matches\n"
            "- TheSportsDB: also empty\n"
            "Check tennisabstract.com to confirm matches are actually running."
        )
        return

    lines = [
        f"Scan complete: {len(all_matches)} matches",
        f"Live: {len(live)}  Scheduled: {len(scheduled)}  Finished: {len(finished)}",
        "",
    ]

    shown = 0
    for m in (live + scheduled + finished):
        if shown >= 12:
            lines.append(f"... and {len(all_matches) - shown} more")
            break
        icon = {"live": "🟢", "scheduled": "🕐", "finished": "✅"}.get(m["status"], "❓")
        score = m.get("score_text") or "not started"
        src = m["external_id"].split("_")[0].upper()
        lines.append(
            f"{icon} [{src}] {m['player1_name']} vs {m['player2_name']} "
            f"({m['tour']} | {m['surface']})"
        )
        if score and score != "not started":
            lines.append(f"   Score: {score}")
        shown += 1

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
        f"DB: {db_live} live | {db_total} updated today | {db_opps} opportunities",
    ]
    await update.message.reply_text("\n".join(lines))


async def cmd_track(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Search ESPN for a player and show their current match details."""
    query = " ".join(ctx.args).strip() if ctx.args else ""
    if not query:
        await update.message.reply_text(
            "Usage: /track player\\_name\nExample: /track Alcaraz",
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
            f"'{query}' not found in ESPN.\n"
            f"ESPN shows {len(all_matches)} events today."
        )
        return

    for m in found[:3]:
        icon = {"live": "🟢", "finished": "✅", "scheduled": "🕐"}.get(m["status"], "❓")
        score = m.get("score_text") or "not started"
        await update.message.reply_text(
            f"{icon} {m['player1_name']} vs {m['player2_name']}\n"
            f"Tour: {m['tour']} | Surface: {m['surface']}\n"
            f"Score: {score}\n"
            f"Status: {m['status']}"
        )


async def cmd_polytest(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Test Polymarket API connectivity and show diagnostic information."""
    await update.message.reply_text("Checking Polymarket connectivity...")

    from app.collectors.polymarket import test_connectivity
    diag = await test_connectivity()

    if diag.get("relay_configured"):
        route_line = f"Relay: {diag['relay_hint']}"
    elif diag.get("proxy_configured"):
        route_line = f"Proxy: {diag['proxy_hint']}"
    else:
        route_line = "No relay configured — Railway IPs may be blocked by Polymarket"

    endpoint_line = f"Endpoint: {diag.get('endpoint', '?')}"

    clob_ok = diag.get("clob_ok", False)
    clob_status = diag.get("clob_status", "?")
    clob_price_ok = diag.get("clob_price_ok")
    clob_fmt = diag.get("clob_price_format", "")
    clob_val = diag.get("clob_price_value", "")
    clob_tried = diag.get("clob_price_formats_tried", "")

    if not clob_ok:
        clob_line = f"CLOB: BLOCKED ({clob_status})"
    elif clob_price_ok is True:
        clob_line = f"CLOB: OK — prices work via [{clob_fmt}]"
        if clob_val:
            clob_line += f" (test price: {clob_val})"
    elif clob_price_ok is False:
        clob_line = f"CLOB: reachable but all price formats failed\n  Tried: {clob_tried}"
    else:
        clob_line = "CLOB: reachable (no token to test prices)"

    if diag["ok"]:
        n_markets = diag.get("markets_found", 0)
        n_events  = diag.get("events_scanned", 0)
        n_mkts_path = diag.get("markets_path_events", 0)
        if n_markets > 0:
            atp_c = diag.get("atp_count", 0)
            wta_c = diag.get("wta_count", 0)
            status_line = (
                f"Gamma API: OK — {n_markets} H2H match markets ({n_events} events scanned, "
                f"ATP:{atp_c} WTA:{wta_c})\n"
                f"  Discovery: /events?tag_id=864 + /markets fallback ({n_mkts_path} events from /markets)"
            )
        else:
            status_line = (
                f"Gamma API: reachable, {n_events} events but 0 H2H matches found\n"
                f"  /markets fallback returned {n_mkts_path} events"
            )
        sample_parts = []
        def _slug_date(s: str) -> str:
            parts = s.rsplit("-", 3)
            return "-".join(parts[-3:]) if len(parts) >= 4 else s
        if diag.get("oldest_slug") and diag.get("newest_slug"):
            oldest_date = _slug_date(diag["oldest_slug"])
            newest_date = _slug_date(diag["newest_slug"])
            sample_parts.append(f"Date range: {oldest_date} → {newest_date}")
        month_dist = diag.get("month_dist", {})
        current_month = diag.get("current_month", "")
        current_count = diag.get("current_month_count", 0)
        if current_month:
            if current_count > 0:
                sample_parts.append(f"Current month ({current_month}): {current_count} H2H markets ✓")
            else:
                sample_parts.append(f"Current month ({current_month}): 0 markets — Polymarket has no live H2H markets for current tournament")
        if month_dist:
            dist_str = "  ".join(f"{m}: {c}" for m, c in month_dist.items())
            sample_parts.append(f"By month: {dist_str}")
        if diag.get("sample_question"):
            sample_parts.append(f"Newest: \"{diag['sample_question']}\"")
        if diag.get("sample_slug"):
            sample_parts.append(f"Slug: {diag['sample_slug']}")
        recent = diag.get("recent_slugs", [])
        if recent:
            sample_parts.append("Recent slugs:\n  " + "\n  ".join(recent[-5:]))
        sample_line = "\n".join(sample_parts)
    else:
        status_line = f"Gamma API: BLOCKED — HTTP {diag['http_status']}"
        sample_line = (
            "\nFix — deploy Cloudflare Worker relay:\n"
            "1. cloudflare.com -> Workers -> Create Worker\n"
            "2. Paste cloudflare-worker.js from the repo\n"
            "3. Save & Deploy -> copy the worker URL\n"
            "4. Railway Variables: POLYMARKET_RELAY_URL=<url>"
        )

    if not clob_ok and diag["ok"]:
        sample_line += (
            "\n\nGamma works but CLOB is blocked.\n"
            "Prices update from Gamma (~1 min lag) — set relay for real-time CLOB prices."
        )

    lines = [route_line, endpoint_line, status_line, clob_line]
    if sample_line:
        lines.append(sample_line)

    if diag["last_cache_error"]:
        lines.append(f"Last error: {diag['last_cache_error'][:80]}")

    async with AsyncSessionLocal() as db:
        from sqlalchemy.orm import selectinload
        result = await db.execute(
            select(Match)
            .options(selectinload(Match.player1), selectinload(Match.player2))
            .where(Match.status == "live")
            .limit(5)
        )
        live = result.scalars().all()

    # Show sample ATP slugs so we can verify the slug format
    atp_slugs = diag.get("atp_slugs_sample", [])
    if atp_slugs:
        lines.append("\nATP slugs (sample):\n  " + "\n  ".join(atp_slugs[:10]))

    if live:
        from app.collectors.polymarket import search_tennis_markets
        lines.append("\nLive matches (live search):")
        for m in live:
            p1 = m.player1.name if m.player1 else "?"
            p2 = m.player2.name if m.player2 else "?"
            found = await search_tennis_markets(p1, p2)
            if found:
                slug = found[0].get("_event_slug", "?")
                lines.append(f"  {p1} vs {p2} -> FOUND: {slug}")
            else:
                lines.append(f"  {p1} vs {p2} -> not found")

    await update.message.reply_text("\n".join(lines))


async def cmd_elostats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show ELO coverage stats and list players missing ELO data."""
    async with AsyncSessionLocal() as db:
        from sqlalchemy.orm import selectinload
        atp_total = (await db.execute(
            select(func.count()).select_from(Player).where(Player.tour == "ATP")
        )).scalar() or 0
        wta_total = (await db.execute(
            select(func.count()).select_from(Player).where(Player.tour == "WTA")
        )).scalar() or 0
        atp_with_elo = (await db.execute(
            select(func.count()).select_from(Player).where(
                Player.tour == "ATP",
                Player.current_elo.isnot(None),
                Player.current_elo != 1500.0,
            )
        )).scalar() or 0
        wta_with_elo = (await db.execute(
            select(func.count()).select_from(Player).where(
                Player.tour == "WTA",
                Player.current_elo.isnot(None),
                Player.current_elo != 1500.0,
            )
        )).scalar() or 0

        # Find live players who are missing ELO
        live_match_q = await db.execute(
            select(Match)
            .options(selectinload(Match.player1), selectinload(Match.player2))
            .where(Match.status == "live")
        )
        live_matches = live_match_q.scalars().all()
        missing: list[str] = []
        for m in live_matches:
            for p in (m.player1, m.player2):
                if p is None:
                    continue
                if p.current_elo is None or abs(p.current_elo - 1500) <= 1:
                    missing.append(f"{p.name} ({p.tour})")

        elo_setting_q = await db.execute(
            select(BotSettings).where(BotSettings.key == "last_elo_refresh")
        )
        elo_setting = elo_setting_q.scalar_one_or_none()

    lines = [
        "ELO coverage:",
        f"  ATP: {atp_with_elo}/{atp_total} players ({atp_with_elo*100//max(atp_total,1)}%)",
        f"  WTA: {wta_with_elo}/{wta_total} players ({wta_with_elo*100//max(wta_total,1)}%)",
        f"  Last refresh: {elo_setting.value if elo_setting else 'never'}",
    ]
    if missing:
        lines += ["", f"Live players without ELO ({len(missing)}):"]
        lines += [f"  - {n}" for n in missing[:20]]
        if len(missing) > 20:
            lines.append(f"  ... and {len(missing) - 20} more")
    else:
        lines += ["", "All live players have ELO data ✓"]

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
            "Usage: /setpoly <player> <condition_id>\n\n"
            "Find the condition_id in the Polymarket market URL:\n"
            "https://polymarket.com/event/{slug}?tid={condition_id}\n\n"
            "Example:\n"
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
            f"No live match found with '{args[0]}'.\n"
            + ("Live matches:\n" + "\n".join(f"  - {n}" for n in names) if names else "No live matches.")
        )
        return

    async with AsyncSessionLocal() as db:
        from sqlalchemy.orm import selectinload
        result = await db.execute(
            select(Match)
            .options(selectinload(Match.player1), selectinload(Match.player2))
            .where(Match.id == match.id)
        )
        m = result.scalar_one()
        m.polymarket_condition_id = condition_id
        if not condition_id.startswith("0x"):
            m.polymarket_slug = condition_id

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
            f"Linked: {p1} vs {p2}\n"
            f"Condition ID: {condition_id[:20]}...\n"
            f"Price: {p1.split()[-1]} {price*100:.0f}% | {p2.split()[-1]} {(1-price)*100:.0f}%"
        )
    else:
        await update.message.reply_text(
            f"Saved condition ID for {p1} vs {p2}: {condition_id[:20]}...\n"
            f"Could not fetch price yet. Verify the ID is correct and the API is reachable."
        )
