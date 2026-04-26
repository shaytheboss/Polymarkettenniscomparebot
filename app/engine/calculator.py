"""
Main probability calculator — runs both models and returns structured result.

Model A = TABLE model  (reference tables from empirical research)
Model B = MARKOV model (full point-by-point Markov chain)

Both take identical input (MatchState) and return WinProbResult.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from app.engine.tables import (
    Tour, Surface, EloBand,
    elo_band, pre_match_prob_from_elo,
    prob_after_set1, lookup_mid_match_state,
    HOLD_RATE_BASELINE, surface_adj_hold,
    surface_adj_comeback, surface_adj_overall,
)
from app.engine.markov import compute_win_probability, elo_to_serve_probs


# ---------------------------------------------------------------------------
# Input / Output types
# ---------------------------------------------------------------------------

@dataclass
class MatchState:
    """
    Complete snapshot of a live tennis match needed for probability calculation.
    Player 1 is treated as the 'favorite' (higher ELO).
    """
    # Player info
    player1_name: str
    player2_name: str
    player1_elo: float
    player2_elo: float
    tour: Tour                    # "ATP" or "WTA"
    surface: Surface              # "hard" / "clay" / "grass"

    # Match score
    p1_sets: int = 0              # sets won by player1
    p2_sets: int = 0
    p1_games: int = 0             # games in current set
    p2_games: int = 0
    p1_pts: int = 0               # points in current game (0-3)
    p2_pts: int = 0
    server: int = 0               # 0 = player1 serving, 1 = player2 serving
    in_tiebreak: bool = False

    # Optional override for serve rate (if known from live stats)
    p1_serve_override: Optional[float] = None
    p2_serve_override: Optional[float] = None


@dataclass
class WinProbResult:
    """Probability estimate from a single model."""
    model_name: str               # "TABLE" or "MARKOV"
    p1_win_prob: float            # P(player1 wins match)
    p2_win_prob: float            # = 1 - p1_win_prob
    elo_band: EloBand
    elo_gap: float
    confidence: str               # [H], [M], or [L]
    notes: str = ""
    p1_serve_prob: Optional[float] = None
    p2_serve_prob: Optional[float] = None


@dataclass
class DualModelResult:
    """Output of both models for a single match state."""
    match_state: MatchState
    table_model: WinProbResult
    markov_model: WinProbResult
    consensus_prob: float         # weighted average of both models
    model_agreement: float        # |table - markov|, lower = more confident

    # Edge detection vs Polymarket
    polymarket_price: Optional[float] = None      # current Polymarket P(p1 wins)
    edge_table: Optional[float] = None            # table_prob - polymarket_price
    edge_markov: Optional[float] = None           # markov_prob - polymarket_price
    edge_consensus: Optional[float] = None        # consensus - polymarket_price
    is_opportunity: bool = False
    opportunity_direction: str = ""               # "BACK_P1", "BACK_P2", ""

    def summary(self) -> str:
        lines = [
            f"{'─'*55}",
            f"Match: {self.match_state.player1_name} vs {self.match_state.player2_name}",
            f"Score: {self.match_state.p1_sets}-{self.match_state.p2_sets} sets | "
            f"{self.match_state.p1_games}-{self.match_state.p2_games} games | "
            f"{self.match_state.p1_pts}-{self.match_state.p2_pts} pts",
            f"ELO gap: {self.match_state.player1_elo - self.match_state.player2_elo:+.0f} "
            f"({self.table_model.elo_band})",
            f"{'─'*55}",
            f"TABLE  model:  P({self.match_state.player1_name[:12]}) = "
            f"{self.table_model.p1_win_prob*100:.1f}%  [{self.table_model.confidence}]",
            f"MARKOV model:  P({self.match_state.player1_name[:12]}) = "
            f"{self.markov_model.p1_win_prob*100:.1f}%  [{self.markov_model.confidence}]",
            f"Consensus:     {self.consensus_prob*100:.1f}%  "
            f"(agreement gap: {self.model_agreement*100:.1f}pp)",
        ]
        if self.polymarket_price is not None:
            lines += [
                f"{'─'*55}",
                f"Polymarket:    {self.polymarket_price*100:.1f}%",
                f"Edge (table):  {(self.edge_table or 0)*100:+.1f}pp",
                f"Edge (Markov): {(self.edge_markov or 0)*100:+.1f}pp",
                f"Edge (cons.):  {(self.edge_consensus or 0)*100:+.1f}pp",
            ]
            if self.is_opportunity:
                lines.append(f"*** OPPORTUNITY: {self.opportunity_direction} ***")
        lines.append("─" * 55)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Table model
# ---------------------------------------------------------------------------

def _table_model(state: MatchState) -> WinProbResult:
    elo_gap = state.player1_elo - state.player2_elo
    band = elo_band(elo_gap)
    tour = state.tour
    surface = state.surface

    base_p = pre_match_prob_from_elo(elo_gap, tour)

    # Surface adjustment
    surf_adj = surface_adj_overall(surface)
    p = base_p + surf_adj

    confidence = "[M]"
    notes = "Pre-match ELO logistic"

    # Adjust based on in-match state
    sets_done = state.p1_sets + state.p2_sets

    if sets_done == 0 and state.p1_games == 0 and state.p1_pts == 0:
        # Match start — pure pre-match
        pass

    elif sets_done >= 1:
        won_s1 = state.p1_sets > state.p2_sets
        p = prob_after_set1(won_s1, band, tour)
        p += surface_adj_comeback(surface) * (0 if won_s1 else 1)
        confidence = "[M]"
        notes = f"After set 1 ({'won' if won_s1 else 'lost'} set 1)"

        # Refine based on current set + game state
        if sets_done == 2:
            # In set 3
            game_diff = state.p1_games - state.p2_games
            is_serving = (state.server == 0)
            if state.p1_games == 5 and state.p2_games == 5:
                # 5-5 in set 3
                if is_serving:
                    tbl = lookup_mid_match_state(8, tour, band)
                    p = tbl if tbl else p
                    notes = "5-5 s3 on serve (state #8)"
                else:
                    p_8 = lookup_mid_match_state(8, tour, band)
                    p = ((p_8 or p) * 0.95) if p_8 else p
                    notes = "5-5 s3 returning"
            elif state.p1_games == 5 and state.p2_games == 4 and is_serving:
                tbl = lookup_mid_match_state(9, tour, band)
                p = tbl if tbl else p
                notes = "5-4 s3 serving for match (state #9)"
            elif state.p1_games == 3 and state.p2_games == 3:
                state_id = 4 if is_serving else 5
                tbl = lookup_mid_match_state(state_id, tour, band)
                p = tbl if tbl else p
                notes = f"3-3 s3 {'serve' if is_serving else 'return'} (state #{state_id})"
            else:
                tbl = lookup_mid_match_state(10, tour, band)
                p = tbl if tbl else p
                notes = f"Set 3 in progress (state #10 baseline), {state.p1_games}-{state.p2_games}"
        elif sets_done == 1:
            # In set 2 with set1 decided
            game_diff = state.p1_games - state.p2_games
            is_serving = (state.server == 0)
            if won_s1:
                if state.p1_games == 3 and state.p2_games == 3 and is_serving:
                    tbl = lookup_mid_match_state(1, tour, band)
                    p = tbl if tbl else p
                    notes = "Up set, 3-3 s2 on serve (state #1)"
                elif state.p1_games == 1 and state.p2_games == 3 and not is_serving:
                    tbl = lookup_mid_match_state(2, tour, band)
                    p = tbl if tbl else p
                    notes = "Up set, broken (1-3 s2 opp serving) (state #2)"
                elif state.p1_games == 3 and state.p2_games == 1 and is_serving:
                    tbl = lookup_mid_match_state(6, tour, band)
                    p = tbl if tbl else p
                    notes = "Up set + break (3-1 s2 serving) (state #6)"
                elif state.p1_games == 1 and state.p2_games == 3 and is_serving:
                    tbl = lookup_mid_match_state(7, tour, band)
                    # state 7 is for losing-down, invert if we're looking at p1 who's UP
                    if tbl is not None:
                        p = 1.0 - tbl
                    notes = "Up set but down break (1-3 s2)"
            else:
                # Lost set 1
                if state.p1_games == 1 and state.p2_games == 1 and is_serving:
                    tbl = lookup_mid_match_state(3, tour, band)
                    p = tbl if tbl else p
                    notes = "Down set, broke back (1-1 s2 on serve) (state #3)"
                elif state.p1_games == 1 and state.p2_games == 3 and not is_serving:
                    tbl = lookup_mid_match_state(7, tour, band)
                    p = tbl if tbl else p
                    notes = "Down set AND break (1-3 s2 returning) (state #7)"
                elif state.p1_games == 3 and state.p2_games == 1 and is_serving:
                    # Down set but up break
                    tbl = lookup_mid_match_state(3, tour, band)
                    p = (tbl or p) + 0.10  # ahead in set 2
                    notes = "Down set, up break s2"

    p = max(0.01, min(0.99, p))
    return WinProbResult(
        model_name="TABLE",
        p1_win_prob=p,
        p2_win_prob=1.0 - p,
        elo_band=band,
        elo_gap=elo_gap,
        confidence=confidence,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Markov model
# ---------------------------------------------------------------------------

def _markov_model(state: MatchState) -> WinProbResult:
    elo_gap = state.player1_elo - state.player2_elo
    band = elo_band(elo_gap)

    if state.p1_serve_override and state.p2_serve_override:
        p1s, p2s = state.p1_serve_override, state.p2_serve_override
    else:
        p1s, p2s = elo_to_serve_probs(
            state.player1_elo, state.player2_elo,
            state.tour, state.surface
        )

    p = compute_win_probability(
        p1_serve=p1s,
        p2_serve=p2s,
        p1_sets=state.p1_sets,
        p2_sets=state.p2_sets,
        p1_games=state.p1_games,
        p2_games=state.p2_games,
        p1_pts=state.p1_pts,
        p2_pts=state.p2_pts,
        server=state.server,
        in_tiebreak=state.in_tiebreak,
    )
    p = max(0.01, min(0.99, p))
    return WinProbResult(
        model_name="MARKOV",
        p1_win_prob=p,
        p2_win_prob=1.0 - p,
        elo_band=band,
        elo_gap=elo_gap,
        confidence="[M]",
        notes=f"Full Markov chain. p1_serve={p1s:.3f}, p2_serve={p2s:.3f}",
        p1_serve_prob=p1s,
        p2_serve_prob=p2s,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def calculate(
    state: MatchState,
    polymarket_price: Optional[float] = None,
    min_edge_pp: float = 5.0,
) -> DualModelResult:
    """
    Run both models on the match state and return dual-model result.

    Args:
        state: Current match state
        polymarket_price: P(player1 wins) according to Polymarket (0-1)
        min_edge_pp: Minimum edge in percentage points to flag as opportunity

    Returns:
        DualModelResult with both model outputs and edge vs Polymarket
    """
    table_result = _table_model(state)
    markov_result = _markov_model(state)

    # Consensus: weighted average (slight Markov weight due to precision)
    consensus = 0.45 * table_result.p1_win_prob + 0.55 * markov_result.p1_win_prob
    agreement = abs(table_result.p1_win_prob - markov_result.p1_win_prob)

    result = DualModelResult(
        match_state=state,
        table_model=table_result,
        markov_model=markov_result,
        consensus_prob=consensus,
        model_agreement=agreement,
        polymarket_price=polymarket_price,
    )

    if polymarket_price is not None:
        result.edge_table = table_result.p1_win_prob - polymarket_price
        result.edge_markov = markov_result.p1_win_prob - polymarket_price
        result.edge_consensus = consensus - polymarket_price

        threshold = min_edge_pp / 100.0
        if result.edge_consensus >= threshold and agreement < 0.15:
            result.is_opportunity = True
            result.opportunity_direction = "BACK_P1"
        elif result.edge_consensus <= -threshold and agreement < 0.15:
            result.is_opportunity = True
            result.opportunity_direction = "BACK_P2"
            # Reframe: if backing P2, edge is how underpriced P2 is
            result.edge_consensus = -result.edge_consensus

    return result
