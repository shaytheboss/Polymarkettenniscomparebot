"""
Background scheduler jobs.

Name matching flow:
  ESPN  →  player name (e.g. "Novak Djokovic")
          ↓ find_player_by_name() fuzzy-matches against DB
  DB Player  →  surface_elo()
  ELO lookup ↓
  Calculator  →  probability
  Polymarket ↓  fetch_match_price() fuzzy-matches market question
  Edge detection → alert
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.match import Match
from app.models.player import Player
from app.models.alert import BotSettings
from app.collectors.tennis_live import fetch_all_today, fetch_upcoming_matches
from app.collectors.elo_collector import refresh_elo, find_player_by_name
from app.utils.name_matcher import _last_name
from app.collectors.polymarket import fetch_match_price, batch_fetch_prices, fetch_best_ask
from app.analyzers.opportunity_detector import process_live_match
from app.bot.telegram_bot import send_opportunity_alert
from app.models.opportunity import Opportunity

logger = logging.getLogger(__name__)

# System-wide alert defaults (from settings, displayed at startup and in heartbeat)
from app.config import settings
_DEFAULTS = {
    "min_edge_pp":        settings.default_min_edge_pp,
    "max_model_gap_pp":   settings.default_max_model_gap_pp,
    "alert_dedup_min":    settings.alert_dedup_minutes,
    "live_scores_sec":    settings.live_scores_interval,
    "polymarket_sec":     settings.polymarket_interval,
    "analyzer_sec":       settings.analyzer_interval,
}

_heartbeat_count = 0  # incremented each call; Telegram update every 48th (≈4 hours)

# Tracks edge_pp at the time of the last alert for each (match_id, back_player).
# Used to suppress re-alerts when the edge hasn't changed materially.
_last_alert_edge: dict[tuple[int, int], float] = {}
MATERIAL_EDGE_CHANGE_PP = 2.0


# ---------------------------------------------------------------------------
# Live scores
# ---------------------------------------------------------------------------

async def job_heartbeat():
    """Log a status heartbeat every 5 minutes; send Telegram update every 30 minutes."""
    global _heartbeat_count
    _heartbeat_count += 1

    from sqlalchemy import func
    from app.models.opportunity import Opportunity

    async with AsyncSessionLocal() as db:
        try:
            live_n = (await db.execute(
                select(func.count()).select_from(Match).where(Match.status == "live")
            )).scalar() or 0
            today_start = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            opps_n = (await db.execute(
                select(func.count()).select_from(Opportunity)
                .where(Opportunity.detected_at >= today_start)
            )).scalar() or 0
            atp_players = (await db.execute(
                select(func.count()).select_from(Player).where(Player.tour == "ATP")
            )).scalar() or 0
            wta_players = (await db.execute(
                select(func.count()).select_from(Player).where(Player.tour == "WTA")
            )).scalar() or 0
        except Exception as e:
            logger.error(f"Heartbeat DB query failed: {e}")
            live_n = opps_n = atp_players = wta_players = -1

    logger.info(
        f"[HEARTBEAT #{_heartbeat_count}] live_matches={live_n} | opps_today={opps_n} "
        f"| players=ATP:{atp_players}/WTA:{wta_players} "
        f"| defaults: edge≥{_DEFAULTS['min_edge_pp']}pp "
        f"model_gap≤{_DEFAULTS['max_model_gap_pp']}pp "
        f"dedup={_DEFAULTS['alert_dedup_min']}min"
    )

    # Send Telegram status update every 48th heartbeat (≈4 hours)
    if _heartbeat_count % 48 == 0:
        try:
            from app.bot.telegram_bot import broadcast_message
            ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
            live_icon = "🟢" if live_n > 0 else "⚪"
            msg = (
                f"Bot status [{ts}]\n"
                f"{live_icon} Live matches: {live_n}\n"
                f"Opportunities today: {opps_n}\n"
                f"Players: ATP {atp_players} | WTA {wta_players}\n"
                f"Thresholds: edge>={_DEFAULTS['min_edge_pp']}pp | gap<={_DEFAULTS['max_model_gap_pp']}pp\n"
                f"\n"
                f"Models:\n"
                f"  TABLE  — win-rate tables from 50K historical matches\n"
                f"  MARKOV — point-by-point simulation using player ELO\n"
                f"  Consensus — weighted average (45% TABLE + 55% MARKOV)"
            )
            asyncio.ensure_future(broadcast_message(msg))
        except Exception as e:
            logger.error(f"Heartbeat Telegram update failed: {e}")


async def job_fetch_live_scores():
    """Poll ESPN live scores and upsert match records."""
    try:
        await _job_fetch_live_scores_inner()
    except Exception as exc:
        logger.error(f"job_fetch_live_scores unhandled: {exc}", exc_info=True)


async def _job_fetch_live_scores_inner():
    raw_matches = await fetch_all_today()
    if not raw_matches:
        logger.warning("fetch_all_today returned 0 matches — all three sources failed")
        return

    by_status: dict[str, int] = {}
    for m in raw_matches:
        by_status[m["status"]] = by_status.get(m["status"], 0) + 1
    logger.info(
        f"fetch_all_today: {len(raw_matches)} matches — "
        + " | ".join(f"{k}:{v}" for k, v in sorted(by_status.items()))
    )

    new_count = updated_count = 0
    live_notifications: list[str] = []   # new + transitioned-to-live matches
    set_won_notifications: list[str] = []  # set completed during live match

    async with AsyncSessionLocal() as db:
        for raw in raw_matches:
            p1_name = raw["player1_name"]
            p2_name = raw["player2_name"]

            # Skip placeholder entries ESPN uses for unconfirmed bracket slots
            if p1_name in ("TBD", "Unknown", "") or p2_name in ("TBD", "Unknown", ""):
                continue

            ext_id = raw["external_id"]
            result = await db.execute(select(Match).where(Match.external_id == ext_id))
            match = result.scalar_one_or_none()

            if match is None:
                p1 = await _resolve_player(p1_name, raw["tour"], db)
                p2 = await _resolve_player(p2_name, raw["tour"], db)
                match = Match(
                    external_id=ext_id,
                    player1_id=p1.id,
                    player2_id=p2.id,
                    tour=raw["tour"],
                    surface=raw["surface"],
                    tournament=raw.get("tournament", ""),
                    round=raw.get("round", ""),
                    p1_elo_at_match=p1.surface_elo(raw["surface"]),
                    p2_elo_at_match=p2.surface_elo(raw["surface"]),
                )
                db.add(match)
                await db.flush()
                logger.info(
                    f"NEW match: {p1_name} vs {p2_name} "
                    f"[{raw['tour']} | {raw['surface']} | {raw.get('tournament','')}] "
                    f"status={raw['status']}"
                )
                new_count += 1
                if raw["status"] == "live":
                    live_notifications.append(
                        _build_live_notification("🟢 Match started!", raw, match)
                    )
            else:
                # Detect transition to live
                if match.status != "live" and raw["status"] == "live":
                    live_notifications.append(
                        _build_live_notification("🔴 Match started!", raw, match)
                    )
                # Detect set completion during live match
                elif match.status == "live" and raw["status"] == "live":
                    old_sets = (match.p1_sets or 0) + (match.p2_sets or 0)
                    new_sets = raw["p1_sets"] + raw["p2_sets"]
                    if new_sets > old_sets:
                        set_won_notifications.append(
                            _build_live_notification("🎾 Set completed!", raw, match)
                        )
                updated_count += 1

            match.status      = raw["status"]
            match.p1_sets     = raw["p1_sets"]
            match.p2_sets     = raw["p2_sets"]
            match.p1_games    = raw["p1_games"]
            match.p2_games    = raw["p2_games"]
            match.p1_pts      = raw["p1_pts"]
            match.p2_pts      = raw["p2_pts"]
            match.server      = raw["server"]
            match.in_tiebreak = raw["in_tiebreak"]
            match.score_text  = raw.get("score_text", "")

        await db.commit()
    logger.info(f"DB upsert complete: {new_count} new, {updated_count} updated")

    from app.bot.telegram_bot import broadcast_message
    for msg in live_notifications + set_won_notifications:
        asyncio.ensure_future(broadcast_message(msg))


# ---------------------------------------------------------------------------
# Notification helpers
# ---------------------------------------------------------------------------

def _poly_url(p1_name: str, p2_name: str) -> str:
    """Generic Polymarket search URL (fallback when slug not available)."""
    last1 = p1_name.split()[-1]
    last2 = p2_name.split()[-1]
    return f"https://polymarket.com/sports/tennis?q={last1}+{last2}"


def _market_url(match, p1_name: str, p2_name: str) -> str:
    """Direct market URL if slug known, otherwise generic search."""
    if getattr(match, "polymarket_slug", None):
        return f"https://polymarket.com/event/{match.polymarket_slug}"
    return _poly_url(p1_name, p2_name)


def _build_live_notification(header: str, raw: dict, match) -> str:
    """Build a Telegram message for a match going live or a set completing."""
    p1 = raw["player1_name"]
    p2 = raw["player2_name"]
    score = raw.get("score_text") or "-"
    surface_emoji = {"hard": "🔵", "clay": "🟤", "grass": "🟢"}.get(raw["surface"], "⚫")
    p1_elo = match.p1_elo_at_match or 1500
    p2_elo = match.p2_elo_at_match or 1500

    # Flag when ELO is missing (default stub value)
    p1_elo_str = f"{p1_elo:.0f}" if abs(p1_elo - 1500) > 1 else "1500 (no data)"
    p2_elo_str = f"{p2_elo:.0f}" if abs(p2_elo - 1500) > 1 else "1500 (no data)"

    if match.last_poly_price_p1 is not None:
        pr = match.last_poly_price_p1
        poly_line = (
            f"\nPolymarket (ask): {p1.split()[-1]} {pr*100:.0f}% | "
            f"{p2.split()[-1]} {(1-pr)*100:.0f}%"
        )
    else:
        poly_line = "\nPolymarket: searching..."

    return (
        f"{header}\n"
        f"🎾 {p1} vs {p2}\n"
        f"{surface_emoji} {raw['tour']} | {raw['surface'].capitalize()}"
        f" | {raw.get('tournament', '')}\n"
        f"Score: {score}\n"
        f"ELO: {p1.split()[-1]} {p1_elo_str} vs {p2.split()[-1]} {p2_elo_str}"
        f"{poly_line}\n"
        f"{_market_url(match, p1, p2)}"
    )


# ---------------------------------------------------------------------------
# Polymarket price refresh
# ---------------------------------------------------------------------------

async def job_fetch_polymarket():
    """Update Polymarket prices for all live matches.

    Two-phase approach (learned from weather-arb-bot):
    Phase 1 — Batch CLOB price fetch: for matches with a known token ID,
               fetch ALL prices in one CLOB API call (most efficient).
    Phase 2 — Per-match discovery: for unlinked matches, search Gamma API
               by player name and store condition ID + token ID + slug.
    """
    from app.bot.telegram_bot import broadcast_message
    from sqlalchemy.orm import selectinload

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Match)
            .options(selectinload(Match.player1), selectinload(Match.player2))
            .where(Match.status == "live")
        )
        matches = result.scalars().all()

        now = datetime.now(timezone.utc)

        # Phase 1: batch CLOB price fetch for already-linked matches
        batch_prices: dict[str, float] = {}
        token_map = {
            m.external_id: m.polymarket_token_id
            for m in matches
            if getattr(m, "polymarket_token_id", None)
        }
        if token_map:
            batch_prices, batch_ok = await batch_fetch_prices(token_map)
            updated_names = []
            for match in matches:
                if match.external_id in batch_prices:
                    match.last_poly_price_p1 = batch_prices[match.external_id]
                    match.poly_updated_at = now
                    name = (match.player1.name.split()[-1] if match.player1 else match.external_id)
                    updated_names.append(f"{name}={batch_prices[match.external_id]*100:.0f}%")
            if updated_names:
                logger.info(f"Batch CLOB prices updated: {', '.join(updated_names)}")
            # When the batch call succeeded but a known token returned no price, the token is
            # stale (likely a V2 contract upgrade changed the token ID).  Clear it so Phase 2
            # re-discovers the current token via Gamma API next cycle.
            # Only do this when batch_ok=True — a network error must not wipe valid tokens.
            if batch_ok:
                for m in matches:
                    if getattr(m, "polymarket_token_id", None) and m.external_id not in batch_prices:
                        name = m.player1.name.split()[-1] if m.player1 else m.external_id
                        logger.info(
                            f"Stale Polymarket token for {name} — clearing for V2 re-discovery "
                            f"(token={str(m.polymarket_token_id)[:16]})"
                        )
                        m.polymarket_token_id = None
                        m.polymarket_condition_id = None
                        m.polymarket_slug = None

        # Was the CLOB batch call actually useful (connected + returned at least one price)?
        clob_up = batch_ok and bool(batch_prices)
        if token_map and not clob_up:
            logger.warning(
                f"CLOB batch failed or returned no prices "
                f"(batch_ok={batch_ok}, n_tokens={len(token_map)}) — "
                f"falling back to Gamma API for price updates"
            )

        # Phase 2: discover / update matches not covered by CLOB batch
        for match in matches:
            try:
                if not match.player1 or not match.player2:
                    continue
                # Skip if CLOB already gave us a fresh price this cycle
                if match.external_id in batch_prices:
                    continue

                p1_name = match.player1.name
                p2_name = match.player2.name

                # When CLOB is down AND this match is already linked, go straight to
                # Gamma — avoids wasting a redundant failing CLOB call first.
                if not clob_up and match.polymarket_condition_id:
                    from app.collectors.polymarket import fetch_price_by_condition_id
                    price = await fetch_price_by_condition_id(
                        match.polymarket_condition_id, p1_name, p2_name
                    )
                    cid = match.polymarket_condition_id
                    slug = getattr(match, "polymarket_slug", None)
                    token_id = getattr(match, "polymarket_token_id", None)
                else:
                    price, cid, slug, token_id = await fetch_match_price(
                        player1=p1_name,
                        player2=p2_name,
                        condition_id=match.polymarket_condition_id,
                        token_id=getattr(match, "polymarket_token_id", None),
                    )

                if price is not None:
                    # Use best ask (actual buying price) when token ID is known.
                    # Cross-validate: only accept ask if it's within 12pp of the CLOB mid.
                    effective_price = price
                    last_trade = None
                    if token_id and clob_up:
                        last_trade = await fetch_best_ask(token_id)
                        if last_trade is not None:
                            if abs(last_trade - price) <= 0.12:
                                effective_price = last_trade
                            else:
                                logger.warning(
                                    f"Best ask {last_trade:.3f} diverges from mid {price:.3f} "
                                    f"for {p1_name} — using mid"
                                )
                                last_trade = None
                    match.last_poly_price_p1 = effective_price
                    match.poly_updated_at = now
                    logger.info(
                        f"Poly price updated: {p1_name.split()[-1]}={effective_price*100:.1f}% "
                        f"({'ask' if last_trade else 'mid/gamma'}) "
                        f"vs {p2_name.split()[-1]}={(1-effective_price)*100:.1f}%"
                    )
                else:
                    logger.warning(
                        f"Poly price unavailable for {p1_name} vs {p2_name} "
                        f"(cid={'set' if match.polymarket_condition_id else 'none'}, "
                        f"token={'set' if getattr(match, 'polymarket_token_id', None) else 'none'})"
                    )

                newly_linked = cid and not match.polymarket_condition_id
                if newly_linked:
                    match.polymarket_condition_id = cid
                    if slug:
                        match.polymarket_slug = slug
                    if token_id:
                        match.polymarket_token_id = token_id
                    logger.info(
                        f"Linked Polymarket market cid={cid} slug={slug} "
                        f"token={token_id} → {p1_name} vs {p2_name}"
                    )

                # Notify when Polymarket is first linked to a live match
                if newly_linked and price is not None:
                    direct_url = _market_url(match, p1_name, p2_name)
                    p1_elo = match.p1_elo_at_match or 1500
                    p2_elo = match.p2_elo_at_match or 1500
                    display_price = effective_price if last_trade else price
                    price_label = "ask" if last_trade else "mid"
                    msg = (
                        f"Polymarket linked!\n"
                        f"🎾 {p1_name} vs {p2_name}\n"
                        f"{p1_name.split()[-1]}: {display_price*100:.1f}% | "
                        f"{p2_name.split()[-1]}: {(1-display_price)*100:.1f}%"
                        f"  ({price_label})\n"
                        f"ELO: {p1_elo:.0f} vs {p2_elo:.0f}\n"
                        f"Score: {match.score_text or '-'}\n"
                        f"{direct_url}"
                    )
                    asyncio.ensure_future(broadcast_message(msg))

            except Exception as e:
                logger.warning(f"Polymarket update failed for match {match.id}: {e}")

        await db.commit()


# ---------------------------------------------------------------------------
# Opportunity analyzer
# ---------------------------------------------------------------------------

async def job_run_analyzer():
    """Run probability calculation and opportunity detection on all live matches."""
    from sqlalchemy.orm import selectinload
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Match)
            .options(
                selectinload(Match.player1),
                selectinload(Match.player2),
                selectinload(Match.snapshots),
            )
            .where(Match.status == "live")
        )
        matches = result.scalars().all()

        for match in matches:
            try:
                if match.last_poly_price_p1 is None:
                    continue

                new_opps, updated_opps, _calc = await process_live_match(match, db)

                # Snapshot ids + edge values before commit (ORM objects expire after commit)
                new_snap    = [(o.id, o.back_player, float(o.edge_pp)) for o in new_opps]
                updated_snap = [(o.id, o.back_player, float(o.edge_pp)) for o in updated_opps]
                await db.commit()

                # New edge event — alert immediately (subject to per-user thresholds in send_opportunity_alert)
                for opp_id, back_player, edge_pp in new_snap:
                    opp = (await db.execute(
                        select(Opportunity).where(Opportunity.id == opp_id)
                    )).scalar_one_or_none()
                    if opp:
                        try:
                            await send_opportunity_alert(opp, match, db)
                        except Exception as e:
                            logger.error(f"Alert send failed: {e}")
                        _last_alert_edge[(match.id, back_player)] = edge_pp

                # Continuing edge event — only re-alert when edge moved materially
                for opp_id, back_player, edge_pp in updated_snap:
                    key = (match.id, back_player)
                    last = _last_alert_edge.get(key, edge_pp)
                    if abs(edge_pp - last) >= MATERIAL_EDGE_CHANGE_PP:
                        opp = (await db.execute(
                            select(Opportunity).where(Opportunity.id == opp_id)
                        )).scalar_one_or_none()
                        if opp:
                            try:
                                await send_opportunity_alert(opp, match, db)
                            except Exception as e:
                                logger.error(f"Re-alert send failed: {e}")
                            _last_alert_edge[key] = edge_pp

            except Exception as e:
                logger.error(f"Analyzer failed for match {match.id}: {e}", exc_info=True)


# ---------------------------------------------------------------------------
# ELO refresh
# ---------------------------------------------------------------------------

async def job_refresh_elo():
    """Daily ELO refresh from Tennis Abstract."""
    async with AsyncSessionLocal() as db:
        try:
            atp_n = await refresh_elo("ATP", db)
            wta_n = await refresh_elo("WTA", db)
            logger.info(f"ELO refresh complete: ATP={atp_n}, WTA={wta_n}")

            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            result = await db.execute(
                select(BotSettings).where(BotSettings.key == "last_elo_refresh")
            )
            setting = result.scalar_one_or_none()
            if setting:
                setting.value = ts
            else:
                db.add(BotSettings(key="last_elo_refresh", value=ts))
            await db.commit()
        except Exception as e:
            logger.error(f"ELO refresh failed: {e}", exc_info=True)


# ---------------------------------------------------------------------------
# Match cleanup
# ---------------------------------------------------------------------------

async def job_mark_finished():
    """Mark matches whose set score shows a winner as finished."""
    from sqlalchemy.orm import selectinload
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Match)
            .options(selectinload(Match.player1), selectinload(Match.player2))
            .where(Match.status == "live")
        )
        matches = result.scalars().all()
        now = datetime.now(timezone.utc)
        just_finished = []
        for match in matches:
            if match.p1_sets == 2 or match.p2_sets == 2:
                winner_id = match.player1_id if match.p1_sets == 2 else match.player2_id
                match.status = "finished"
                match.finished_at = now
                match.winner_id = winner_id
                just_finished.append((
                    match.id, winner_id, match.player1_id,
                    match.player1.name if match.player1 else "P1",
                    match.player2.name if match.player2 else "P2",
                    match.score_text or f"{match.p1_sets}-{match.p2_sets}",
                ))
        await db.commit()

    for args in just_finished:
        match_id = args[0]
        # Clean up in-memory alert tracking so stale entries don't accumulate
        for k in [k for k in _last_alert_edge if k[0] == match_id]:
            del _last_alert_edge[k]
        asyncio.ensure_future(_send_match_summary(*args))


async def _send_match_summary(
    match_id: int,
    winner_player_id: int,
    p1_id: int,
    p1_name: str,
    p2_name: str,
    score_text: str,
) -> None:
    """Resolve opportunity outcomes and broadcast match completion summary."""
    from app.bot.telegram_bot import broadcast_message
    from app.models.opportunity import Opportunity

    winner_name = p1_name if winner_player_id == p1_id else p2_name
    winner_is_p1 = (winner_player_id == p1_id)

    async with AsyncSessionLocal() as db:
        opps_result = await db.execute(
            select(Opportunity)
            .where(Opportunity.match_id == match_id)
            .order_by(Opportunity.detected_at)
        )
        opps = opps_result.scalars().all()

        now = datetime.now(timezone.utc)
        for opp in opps:
            if not opp.resolved:
                backed_p1 = (opp.back_player == 1)
                opp.outcome = "WIN" if (backed_p1 == winner_is_p1) else "LOSS"
                opp.resolved = True
                opp.resolved_at = now

        # Snapshot data as plain values BEFORE commit — ORM objects expire after commit
        # and accessing attributes outside the session causes DetachedInstanceError.
        opp_rows = [
            (
                opp.outcome or "?",
                opp.back_player_name or "?",
                float(opp.consensus_prob or 0),
                float(opp.poly_price or 0),
                float(opp.edge_pp or 0),
                opp.score_text or "",
            )
            for opp in opps
        ]
        await db.commit()

    if not opp_rows:
        return

    wins   = sum(1 for outcome, *_ in opp_rows if outcome == "WIN")
    losses = sum(1 for outcome, *_ in opp_rows if outcome == "LOSS")

    lines = [
        f"Match finished",
        f"🎾 {p1_name} vs {p2_name}",
        f"Winner: {winner_name}  |  {score_text}",
        f"",
        f"Opportunities detected ({len(opp_rows)}):",
    ]
    for outcome, back_name, cons_prob, poly_price, edge_pp, score in opp_rows:
        icon = "WIN" if outcome == "WIN" else "LOSS"
        lines.append(f"  {icon}  Backed: {back_name}")
        lines.append(
            f"       Consensus {cons_prob*100:.0f}% vs Poly {poly_price*100:.0f}%"
            f"  |  Edge {edge_pp:+.1f}pp"
        )
        if score:
            lines.append(f"       At score: {score}")
    lines += [f"", f"Total: {wins} WIN  {losses} LOSS"]

    asyncio.ensure_future(broadcast_message("\n".join(lines)))


# ---------------------------------------------------------------------------
# Helper: resolve player from name with fuzzy matching
# ---------------------------------------------------------------------------

async def _resolve_player(name: str, tour: str, db) -> Player:
    """
    Find a Player record by name using fuzzy matching.
    Creates a new stub with ELO=1500 if no match found anywhere.

    Search order:
      1. Exact name + tour match
      2. Fuzzy name + tour match (catches typos/accent differences)
      3. Exact name only (catches ESPN wrong-tour assignments, e.g. WTA player in ATP data)
      4. Create new stub (safe: no IntegrityError because step 3 prevents duplicate names)
    """
    # Step 1+2: fuzzy match within same tour
    player = await find_player_by_name(name, tour, db, fuzzy_threshold=0.80)
    if player:
        return player

    # Step 3: exact name match ignoring tour
    # Handles ESPN misassigning tour (e.g. 'Dominika Salkova' appearing as ATP)
    result = await db.execute(select(Player).where(Player.name == name))
    existing = result.scalar_one_or_none()
    if existing:
        logger.debug(
            f"Player '{name}' found in DB as {existing.tour} "
            f"(ESPN reported tour={tour}) — reusing existing record"
        )
        return existing

    # Step 4: not found — create a stub; ELO refresh will fill it in later
    player = Player(name=name, tour=tour, current_elo=1500.0)
    player.name_last = _last_name(name)  # Required for fast ELO refresh lookups
    db.add(player)
    await db.flush()
    logger.info(f"Created new player stub: {name} ({tour})")
    return player
