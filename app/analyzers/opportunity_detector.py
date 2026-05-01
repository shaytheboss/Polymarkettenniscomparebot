"""
Core opportunity detector.
For each live match: compute dual-model probability, compare to Polymarket,
detect edges, persist opportunities, and return them for alerting.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.engine.calculator import calculate, MatchState
from app.engine.tables import elo_band as get_elo_band
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


def _detect_edge_type(
    p1_sets: int, p2_sets: int, elo_band: str, surface: str, score_text: str
) -> str:
    """Classify which of the 3 documented edges this is, if any."""
    # Edge 1: heavy favorite lost set 1
    if elo_band in ("E2", "E3") and p1_sets == 0 and p2_sets == 1:
        return "SET1_LOSS_HEAVY_FAV"
    # Edge 3: serving for match (5-4 in set 3)
    if p1_sets == 1 and p2_sets == 1 and "5-4" in (score_text or ""):
        return "SERVING_FOR_MATCH"
    # Edge 2: WTA rebreak (detected separately from market price context, flagged generically)
    return "OTHER"


def _kelly(prob: float, poly_price: float) -> float:
    """Full Kelly fraction, clamped 0-0.25."""
    if poly_price <= 0 or poly_price >= 1:
        return 0.0
    b = (1.0 / poly_price) - 1.0
    q = 1.0 - prob
    k = (prob * b - q) / b
    return max(0.0, min(0.25, k))


async def process_live_match(
    match: Match,
    db: AsyncSession,
):
    """
    Run probability calculation for a live match and detect opportunities.
    Returns (list[Opportunity], DualModelResult | None).
    DualModelResult is returned regardless of whether an edge was found,
    so callers can show live probabilities for debugging.
    """
    if match.status != "live":
        return [], None
    if not match.player1 or not match.player2:
        return [], None

    p1 = match.player1
    p2 = match.player2
    p1_elo = float(match.p1_elo_at_match or p1.surface_elo(match.surface))
    p2_elo = float(match.p2_elo_at_match or p2.surface_elo(match.surface))

    # Normalise: player1 in our model = higher ELO (the "favorite")
    swapped = False
    if p2_elo > p1_elo:
        p1, p2 = p2, p1
        p1_elo, p2_elo = p2_elo, p1_elo
        swapped = True

    elo_gap = p1_elo - p2_elo
    band = get_elo_band(elo_gap)

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

    poly_price = match.last_poly_price_p1
    if swapped and poly_price is not None:
        poly_price = 1.0 - poly_price

    # Warn if the stored Polymarket price is suspiciously old
    if poly_price is not None and match.poly_updated_at:
        ts = match.poly_updated_at
        if ts.tzinfo is None:
            from datetime import timezone as _tz
            ts = ts.replace(tzinfo=_tz.utc)
        age_min = (datetime.now(timezone.utc) - ts).total_seconds() / 60
        if age_min > 3:
            logger.warning(
                f"Poly price for {p1.name} vs {p2.name} is {age_min:.0f}min old "
                f"(val={poly_price*100:.1f}%) — may be stale, edge unreliable"
            )

    result = calculate(
        state=state,
        polymarket_price=poly_price,
        min_edge_pp=settings.default_min_edge_pp,
    )

    # Serve probs from Markov model
    p1_serve = result.markov_model.p1_serve_prob
    p2_serve = result.markov_model.p2_serve_prob

    # Save snapshot
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
        p1_serve_prob=p1_serve,
        p2_serve_prob=p2_serve,
        elo_gap=elo_gap,
        raw_data={
            "table_notes": result.table_model.notes,
            "markov_notes": result.markov_model.notes,
            "elo_band": band,
        },
    )
    db.add(snapshot)

    new_opportunities: list[Opportunity] = []

    # Sanity check: an edge > 35pp almost always means the Polymarket token is
    # assigned to the wrong player (inverted price).  Log a hard warning.
    if poly_price is not None:
        raw_edge = abs(result.consensus_prob - poly_price)
        if raw_edge > 0.35:
            logger.warning(
                f"SUSPICIOUS EDGE {raw_edge*100:.0f}pp for "
                f"{p1.name} vs {p2.name} — "
                f"consensus={result.consensus_prob*100:.0f}% poly={poly_price*100:.0f}% "
                f"swapped={swapped}. Polymarket token may be inverted. Skipping."
            )
            return (new_opportunities, result)

    if not result.is_opportunity or poly_price is None:
        return (new_opportunities, result)

    edge_pp = abs(result.edge_consensus or 0) * 100

    # Dedup: skip if same direction at the exact same game score was already recorded.
    # Score-based (not time-based) so the same game state never creates two rows,
    # but a new game with a persistent edge does create a fresh opportunity.
    back_player_num = 1 if result.opportunity_direction == "BACK_P1" else 2
    existing = await db.execute(
        select(Opportunity).where(
            Opportunity.match_id == match.id,
            Opportunity.back_player == back_player_num,
            Opportunity.p1_sets == match.p1_sets,
            Opportunity.p2_sets == match.p2_sets,
            Opportunity.p1_games == match.p1_games,
            Opportunity.p2_games == match.p2_games,
        )
    )
    if existing.scalar_one_or_none():
        return (new_opportunities, result)

    # Map direction back to original player ordering
    if result.opportunity_direction == "BACK_P1":
        back_player = 1 if not swapped else 2
        back_name = p1.name
        opp_prob = result.table_model.p1_win_prob
        opp_markov = result.markov_model.p1_win_prob
        opp_consensus = result.consensus_prob
        opp_poly = poly_price
    else:
        back_player = 2 if not swapped else 1
        back_name = p2.name
        opp_prob = result.table_model.p2_win_prob
        opp_markov = result.markov_model.p2_win_prob
        opp_consensus = 1.0 - result.consensus_prob
        opp_poly = 1.0 - poly_price

    edge_type = _detect_edge_type(
        state.p1_sets, state.p2_sets, band, match.surface, match.score_text or ""
    )
    kelly = _kelly(opp_consensus, opp_poly)

    opp = Opportunity(
        match_id=match.id,
        back_player=back_player,
        back_player_name=back_name,
        table_prob=opp_prob,
        markov_prob=opp_markov,
        consensus_prob=opp_consensus,
        poly_price=opp_poly,
        edge_pp=edge_pp,
        model_agreement=result.model_agreement * 100,
        elo_gap=elo_gap,
        elo_band=band,
        surface=match.surface,
        tour=match.tour,
        edge_category=_edge_category(edge_pp),
        edge_type=edge_type,
        p1_serve_prob=p1_serve,
        p2_serve_prob=p2_serve,
        kelly_fraction=kelly,
        score_text=match.score_text,
        p1_sets=match.p1_sets,
        p2_sets=match.p2_sets,
        p1_games=match.p1_games,
        p2_games=match.p2_games,
        extra={
            "elo_band": band,
            "table_notes": result.table_model.notes,
            "surface": match.surface,
            "tournament": match.tournament,
            "edge_type": edge_type,
        },
    )
    db.add(opp)
    new_opportunities.append(opp)

    logger.info(
        f"OPPORTUNITY [{edge_type}]: {back_name} | edge={edge_pp:.1f}pp "
        f"| consensus={opp_consensus*100:.1f}% vs poly={opp_poly*100:.1f}% "
        f"| kelly={kelly:.1%} | {match.score_text}"
    )

    return (new_opportunities, result)
