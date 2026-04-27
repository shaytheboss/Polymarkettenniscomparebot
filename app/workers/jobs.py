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
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.match import Match
from app.models.player import Player
from app.models.alert import BotSettings
from app.collectors.tennis_live import fetch_live_matches, fetch_upcoming_matches
from app.collectors.elo_collector import refresh_elo, find_player_by_name
from app.collectors.polymarket import fetch_match_price
from app.analyzers.opportunity_detector import process_live_match
from app.bot.telegram_bot import send_opportunity_alert

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Live scores
# ---------------------------------------------------------------------------

async def job_heartbeat():
    """Log a heartbeat so we can confirm the scheduler is alive."""
    logger.info("Heartbeat — scheduler alive")


async def job_fetch_live_scores():
    """Poll ESPN live scores and upsert match records."""
    try:
        await _job_fetch_live_scores_inner()
    except Exception as exc:
        logger.error(f"job_fetch_live_scores unhandled: {exc}", exc_info=True)


async def _job_fetch_live_scores_inner():
    raw_matches = await fetch_live_matches()
    if not raw_matches:
        return

    async with AsyncSessionLocal() as db:
        for raw in raw_matches:
            ext_id = raw["external_id"]
            result = await db.execute(select(Match).where(Match.external_id == ext_id))
            match = result.scalar_one_or_none()

            if match is None:
                p1 = await _resolve_player(raw["player1_name"], raw["tour"], db)
                p2 = await _resolve_player(raw["player2_name"], raw["tour"], db)
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

            # Always refresh score
            match.status     = raw["status"]
            match.p1_sets    = raw["p1_sets"]
            match.p2_sets    = raw["p2_sets"]
            match.p1_games   = raw["p1_games"]
            match.p2_games   = raw["p2_games"]
            match.p1_pts     = raw["p1_pts"]
            match.p2_pts     = raw["p2_pts"]
            match.server     = raw["server"]
            match.in_tiebreak = raw["in_tiebreak"]
            match.score_text = raw.get("score_text", "")

        await db.commit()


# ---------------------------------------------------------------------------
# Polymarket price refresh
# ---------------------------------------------------------------------------

async def job_fetch_polymarket():
    """Update Polymarket prices for all live matches using fuzzy name search."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Match).where(Match.status == "live"))
        matches = result.scalars().all()

        for match in matches:
            try:
                # Ensure player names loaded
                if not match.player1:
                    await db.refresh(match, ["player1", "player2"])
                if not match.player1 or not match.player2:
                    continue

                p1_name = match.player1.name
                p2_name = match.player2.name

                price, cid = await fetch_match_price(
                    player1=p1_name,
                    player2=p2_name,
                    condition_id=match.polymarket_condition_id,
                )

                if price is not None:
                    match.last_poly_price_p1 = price
                    match.poly_updated_at = datetime.now(timezone.utc)

                if cid and not match.polymarket_condition_id:
                    match.polymarket_condition_id = cid
                    logger.info(f"Linked Polymarket market {cid} to {p1_name} vs {p2_name}")

            except Exception as e:
                logger.debug(f"Polymarket update failed for match {match.id}: {e}")

        await db.commit()


# ---------------------------------------------------------------------------
# Opportunity analyzer
# ---------------------------------------------------------------------------

async def job_run_analyzer():
    """Run probability calculation and opportunity detection on all live matches."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Match).where(Match.status == "live")
        )
        matches = result.scalars().all()

        for match in matches:
            try:
                if not match.player1:
                    await db.refresh(match, ["player1", "player2", "snapshots"])

                # Skip if no Polymarket price yet (no edge to detect)
                if match.last_poly_price_p1 is None:
                    continue

                new_opps = await process_live_match(match, db)
                await db.commit()

                for opp in new_opps:
                    try:
                        await send_opportunity_alert(opp, match, db)
                    except Exception as e:
                        logger.error(f"Alert send failed: {e}")
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
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Match).where(Match.status == "live"))
        matches = result.scalars().all()
        now = datetime.now(timezone.utc)
        for match in matches:
            if match.p1_sets == 2 or match.p2_sets == 2:
                match.status = "finished"
                match.finished_at = now
                match.winner_id = (
                    match.player1_id if match.p1_sets == 2 else match.player2_id
                )
        await db.commit()


# ---------------------------------------------------------------------------
# Helper: resolve player from name with fuzzy matching
# ---------------------------------------------------------------------------

async def _resolve_player(name: str, tour: str, db) -> Player:
    """
    Find a Player record by name using fuzzy matching.
    Creates a new record with ELO=1500 if no match found.
    Also refreshes ELO from match if player was just created.
    """
    player = await find_player_by_name(name, tour, db, fuzzy_threshold=0.80)
    if player:
        return player

    # Not found — create a stub. ELO refresh will fill it in later.
    player = Player(name=name, tour=tour, current_elo=1500.0)
    db.add(player)
    await db.flush()
    logger.info(f"Created new player stub: {name} ({tour})")
    return player
