"""Background scheduler jobs."""
from __future__ import annotations
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update as sql_update

from app.database import AsyncSessionLocal
from app.models.match import Match
from app.models.player import Player
from app.models.alert import BotSettings
from app.collectors.tennis_live import fetch_live_matches, fetch_upcoming_matches
from app.collectors.elo_collector import refresh_elo
from app.collectors.polymarket import fetch_market_price, search_tennis_markets
from app.analyzers.opportunity_detector import process_live_match
from app.bot.telegram_bot import send_opportunity_alert

logger = logging.getLogger(__name__)


async def job_fetch_live_scores():
    """Poll live scores and update match records."""
    raw_matches = await fetch_live_matches()
    if not raw_matches:
        return

    async with AsyncSessionLocal() as db:
        for raw in raw_matches:
            ext_id = raw["external_id"]
            result = await db.execute(
                select(Match).where(Match.external_id == ext_id)
            )
            match = result.scalar_one_or_none()

            if match is None:
                # Resolve player IDs
                p1 = await _get_or_create_player(raw["player1_name"], raw["tour"], db)
                p2 = await _get_or_create_player(raw["player2_name"], raw["tour"], db)
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

            # Update live score
            match.status = raw["status"]
            match.p1_sets = raw["p1_sets"]
            match.p2_sets = raw["p2_sets"]
            match.p1_games = raw["p1_games"]
            match.p2_games = raw["p2_games"]
            match.p1_pts = raw["p1_pts"]
            match.p2_pts = raw["p2_pts"]
            match.server = raw["server"]
            match.in_tiebreak = raw["in_tiebreak"]
            match.score_text = raw.get("score_text", "")

            if match.player1 is None:
                await db.refresh(match, ["player1", "player2"])

            await db.commit()


async def job_run_analyzer():
    """Run probability calculation and opportunity detection on all live matches."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Match)
            .where(Match.status == "live")
        )
        matches = result.scalars().all()

        for match in matches:
            try:
                # Ensure relationships loaded
                if not match.player1:
                    await db.refresh(match, ["player1", "player2", "snapshots"])

                new_opps = await process_live_match(match, db)
                await db.commit()

                for opp in new_opps:
                    try:
                        await send_opportunity_alert(opp, match, db)
                    except Exception as e:
                        logger.error(f"Alert send failed: {e}")
            except Exception as e:
                logger.error(f"Analyzer failed for match {match.id}: {e}", exc_info=True)


async def job_fetch_polymarket():
    """Update Polymarket prices for all live matches."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Match).where(Match.status == "live")
        )
        matches = result.scalars().all()

        for match in matches:
            try:
                if match.polymarket_condition_id:
                    price = await fetch_market_price(match.polymarket_condition_id)
                    if price is not None:
                        match.last_poly_price_p1 = price
                        match.poly_updated_at = datetime.now(timezone.utc)
                else:
                    # Try to auto-discover market
                    if not match.player1:
                        await db.refresh(match, ["player1", "player2"])
                    if match.player1 and match.player2:
                        markets = await search_tennis_markets(
                            match.player1.name, match.player2.name
                        )
                        if markets:
                            cid = markets[0].get("conditionId") or markets[0].get("condition_id")
                            if cid:
                                match.polymarket_condition_id = cid
                                price = await fetch_market_price(cid)
                                if price is not None:
                                    match.last_poly_price_p1 = price
                                    match.poly_updated_at = datetime.now(timezone.utc)
            except Exception as e:
                logger.debug(f"Polymarket update failed for match {match.id}: {e}")

        await db.commit()


async def job_refresh_elo():
    """Daily ELO refresh from Tennis Abstract."""
    async with AsyncSessionLocal() as db:
        try:
            atp_count = await refresh_elo("ATP", db)
            wta_count = await refresh_elo("WTA", db)
            logger.info(f"ELO refresh: ATP={atp_count}, WTA={wta_count}")

            # Record timestamp
            result = await db.execute(
                select(BotSettings).where(BotSettings.key == "last_elo_refresh")
            )
            setting = result.scalar_one_or_none()
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            if setting:
                setting.value = ts
            else:
                db.add(BotSettings(key="last_elo_refresh", value=ts))
            await db.commit()
        except Exception as e:
            logger.error(f"ELO refresh failed: {e}", exc_info=True)


async def job_mark_finished():
    """Mark matches as finished if sets=2-0 or 2-1."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Match).where(Match.status == "live")
        )
        matches = result.scalars().all()
        now = datetime.now(timezone.utc)
        for match in matches:
            if match.p1_sets == 2 or match.p2_sets == 2:
                match.status = "finished"
                match.finished_at = now
                if match.p1_sets == 2:
                    match.winner_id = match.player1_id
                else:
                    match.winner_id = match.player2_id
        await db.commit()


async def _get_or_create_player(name: str, tour: str, db) -> Player:
    from sqlalchemy import select
    result = await db.execute(select(Player).where(Player.name == name))
    player = result.scalar_one_or_none()
    if player is None:
        player = Player(name=name, tour=tour, current_elo=1500.0)
        db.add(player)
        await db.flush()
    return player
