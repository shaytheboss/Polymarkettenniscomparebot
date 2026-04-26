"""
Core opportunity detector.
For each live match:
  1. Compute dual-model probability
  2. Compare to Polymarket price
  3. Detect edges and persist opportunities
  4. Return new opportunities for alerting
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.engine.calculator import calculate, MatchState
from app.models.match import Match, MatchSnapshot
from app.models.opportunity import Opportunity
from app.models.player import Player

logger = logging.getLogger(__name__)


def _edge_category(edge_pp: float) -> str:
    if edge_pp >= 15:
        return "STRONG"
    if edge_pp >= 8:
        return "MODERATE"
    return "WEAK"


async def process_live_match(
    match: Match,
    db: AsyncSession,
) -> list[Opportunity]:
    """
    Run probability calculation for a live match and detect opportunities.
    Returns list of new Opportunity objects (already added to session).
    """
    if match.status != "live":
        return []

    if not match.player1 or not match.player2:
        return []

    # Build MatchState (player1 = higher ELO = "favorite")
    p1 = match.player1
    p2 = match.player2
    p1_elo = p1.surface_elo(match.surface)
    p2_elo = p2.surface_elo(match.surface)

    # If p2 has higher ELO, swap so player1 is always the "favorite" in the model
    swapped = False
    if p2_elo > p1_elo:
        p1, p2 = p2, p1
        p1_elo, p2_elo = p2_elo, p1_elo
        swapped = True

    state = MatchState(
        player1_name=p1.name,
        player2_name=p2.name,
        player1_elo=p1_elo,
        player2_elo=p2_elo,
        tour=match.tour,
        surface=match.surface,
        p1_sets=match.p1_sets if not swapped else match.p2_sets,
        p2_sets=match.p2_sets if not swapped else match.p1_sets,
        p1_games=match.p1_games if not swapped else match.p2_games,
        p2_games=match.p2_games if not swapped else match.p1_games,
        p1_pts=match.p1_pts if not swapped else match.p2_pts,
        p2_pts=match.p2_pts if not swapped else match.p1_pts,
        server=match.server if not swapped else (1 - match.server),
        in_tiebreak=match.in_tiebreak,
    )

    # Polymarket price (from match record, updated by polymarket job)
    poly_price = match.last_poly_price_p1
    if swapped and poly_price is not None:
        poly_price = 1.0 - poly_price  # flip to represent favorite

    result = calculate(
        state=state,
        polymarket_price=poly_price,
        min_edge_pp=settings.default_min_edge_pp,
    )

    # Always save snapshot
    snapshot = MatchSnapshot(
        match_id=match.id,
        p1_sets=state.p1_sets,
        p2_sets=state.p2_sets,
        p1_games=state.p1_games,
        p2_games=state.p2_games,
        p1_pts=state.p1_pts,
        p2_pts=state.p2_pts,
        server=state.server,
        in_tiebreak=state.in_tiebreak,
        table_prob_p1=result.table_model.p1_win_prob,
        markov_prob_p1=result.markov_model.p1_win_prob,
        consensus_prob_p1=result.consensus_prob,
        model_agreement=result.model_agreement,
        poly_price_p1=poly_price,
        edge_consensus=result.edge_consensus,
        raw_data={
            "table_notes": result.table_model.notes,
            "markov_notes": result.markov_model.notes,
            "elo_band": result.table_model.elo_band,
        },
    )
    db.add(snapshot)

    new_opportunities: list[Opportunity] = []

    if not result.is_opportunity or poly_price is None:
        return new_opportunities

    edge_pp = abs(result.edge_consensus or 0) * 100

    # Check for duplicate alert within dedup window
    dedup_since = datetime.now(timezone.utc) - timedelta(minutes=settings.alert_dedup_minutes)
    existing = await db.execute(
        select(Opportunity).where(
            Opportunity.match_id == match.id,
            Opportunity.back_player == (1 if result.opportunity_direction == "BACK_P1" else 2),
            Opportunity.detected_at >= dedup_since,
        )
    )
    if existing.scalar_one_or_none():
        return new_opportunities

    # Map direction back to actual player
    if result.opportunity_direction == "BACK_P1":
        back_player = 1 if not swapped else 2
        back_name = p1.name
    else:
        back_player = 2 if not swapped else 1
        back_name = p2.name

    opp = Opportunity(
        match_id=match.id,
        back_player=back_player,
        back_player_name=back_name,
        table_prob=result.table_model.p1_win_prob if back_player == 1 else result.table_model.p2_win_prob,
        markov_prob=result.markov_model.p1_win_prob if back_player == 1 else result.markov_model.p2_win_prob,
        consensus_prob=result.consensus_prob if back_player == 1 else 1 - result.consensus_prob,
        poly_price=poly_price if back_player == 1 else 1 - poly_price,
        edge_pp=edge_pp,
        model_agreement=result.model_agreement * 100,
        score_text=match.score_text,
        p1_sets=match.p1_sets,
        p2_sets=match.p2_sets,
        p1_games=match.p1_games,
        p2_games=match.p2_games,
        edge_category=_edge_category(edge_pp),
        extra={
            "elo_band": result.table_model.elo_band,
            "table_notes": result.table_model.notes,
            "surface": match.surface,
            "tournament": match.tournament,
        },
    )
    db.add(opp)
    new_opportunities.append(opp)

    logger.info(
        f"OPPORTUNITY: {back_name} | edge={edge_pp:.1f}pp | "
        f"consensus={result.consensus_prob*100:.1f}% vs poly={poly_price*100:.1f}% | "
        f"match={match.score_text}"
    )

    return new_opportunities
