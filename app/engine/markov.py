"""
Full point-by-point Markov chain tennis probability calculator.
Implements exact recursive DP for Bo3 matches with tiebreaks.

State: (p1_sets, p2_sets, p1_games, p2_games, p1_pts, p2_pts, server)
  server: 0 = player1 serving, 1 = player2 serving
  Points encoded: 0,1,2,3 = 0,15,30,40. Deuce handled via (3,3) with advantage flag.
"""
from __future__ import annotations
from functools import lru_cache
from typing import NamedTuple


class ServeParams(NamedTuple):
    """Per-point serve win probabilities for each player."""
    p1_serve: float   # P(p1 wins point | p1 serving)
    p2_serve: float   # P(p2 wins point | p2 serving)


# ---------------------------------------------------------------------------
# Game-level probability
# ---------------------------------------------------------------------------

@lru_cache(maxsize=512)
def _p_win_game_from(server_pts: int, returner_pts: int, p_serve: float) -> float:
    """
    P(server wins game) given current score and p_serve = P(server wins each point).
    Uses (server_pts, returner_pts) where 0-3 = 0/15/30/40, deuce handled via (3,3).
    After deuce the state loops until one player gets +2.
    """
    q = 1.0 - p_serve

    # Terminal states
    if server_pts == 4:
        return 1.0
    if returner_pts == 4:
        return 0.0

    # Deuce: recurse using advantage equivalence
    if server_pts == 3 and returner_pts == 3:
        # From deuce: P(server wins) = p^2 / (p^2 + q^2)
        return (p_serve ** 2) / (p_serve ** 2 + q ** 2)

    # Win point → score+1, else opponent +1
    p_win_if_win = _p_win_game_from(server_pts + 1, returner_pts, p_serve)
    p_win_if_lose = _p_win_game_from(server_pts, returner_pts + 1, p_serve)
    return p_serve * p_win_if_win + q * p_win_if_lose


def p_win_game(p_serve: float) -> float:
    """P(server wins game from 0-0 given serve point win rate)."""
    return _p_win_game_from(0, 0, p_serve)


def p_win_game_from_score(server_pts: int, returner_pts: int, p_serve: float) -> float:
    """P(server wins game from given score state."""
    # Clamp to deuce if both ≥3
    sp = min(server_pts, 3)
    rp = min(returner_pts, 3)
    # Advantage states: server leads after deuce = (4,3)-like → use (4,3) magic value
    if server_pts > 3 and returner_pts <= 3:
        return 1.0
    if returner_pts > 3 and server_pts <= 3:
        return 0.0
    return _p_win_game_from(sp, rp, p_serve)


# ---------------------------------------------------------------------------
# Tiebreak probability
# ---------------------------------------------------------------------------

@lru_cache(maxsize=2048)
def _p_win_tiebreak_from(p1_pts: int, p2_pts: int, server: int,
                          p1_serve: float, p2_serve: float) -> float:
    """
    P(player1 wins tiebreak) from (p1_pts, p2_pts) state.
    In a tiebreak service alternates every 2 points starting from 1 after opening.
    server: 0=p1, 1=p2 currently serving this point.
    First to 7, win by 2. After 6-6: alternates every point.
    """
    # Terminal states
    if p1_pts >= 7 and p1_pts - p2_pts >= 2:
        return 1.0
    if p2_pts >= 7 and p2_pts - p1_pts >= 2:
        return 0.0

    p_win_pt = p1_serve if server == 0 else (1.0 - p2_serve)

    # Determine next server
    total = p1_pts + p2_pts
    # First point: original server serves; then alternates every 2
    # After 6-6: alternate every point
    if total >= 12:
        next_server = 1 - server  # alternate every point after 6-6
    else:
        # Changes every 2 points after the first
        if total == 0:
            next_server = server
        elif (total % 2) == 0:
            next_server = server
        else:
            next_server = 1 - server

    p_win_if_win = _p_win_tiebreak_from(p1_pts + 1, p2_pts, next_server, p1_serve, p2_serve)
    p_win_if_lose = _p_win_tiebreak_from(p1_pts, p2_pts + 1, next_server, p1_serve, p2_serve)
    return p_win_pt * p_win_if_win + (1 - p_win_pt) * p_win_if_lose


def p_win_tiebreak(server: int, p1_serve: float, p2_serve: float) -> float:
    """P(player1 wins tiebreak) from 0-0, given who serves first."""
    return _p_win_tiebreak_from(0, 0, server, p1_serve, p2_serve)


# ---------------------------------------------------------------------------
# Set-level probability
# ---------------------------------------------------------------------------

@lru_cache(maxsize=4096)
def _p_win_set_from(p1_games: int, p2_games: int, server: int,
                    p1_serve: float, p2_serve: float) -> float:
    """
    P(player1 wins set) from current game score.
    Standard set: first to 6, win by 2. Tiebreak at 6-6.
    server: 0=p1, 1=p2 currently serving next game.
    """
    # Terminal
    if p1_games >= 6 and p1_games - p2_games >= 2:
        return 1.0
    if p2_games >= 6 and p2_games - p1_games >= 2:
        return 0.0
    if p1_games == 7:
        return 1.0
    if p2_games == 7:
        return 0.0

    # Tiebreak at 6-6
    if p1_games == 6 and p2_games == 6:
        return p_win_tiebreak(server, p1_serve, p2_serve)

    # Compute probability p1 wins current game
    if server == 0:
        p1_wins_game = p_win_game(p1_serve)
    else:
        p1_wins_game = 1.0 - p_win_game(p2_serve)

    next_server = 1 - server  # service alternates each game

    p_set_if_win = _p_win_set_from(p1_games + 1, p2_games, next_server, p1_serve, p2_serve)
    p_set_if_lose = _p_win_set_from(p1_games, p2_games + 1, next_server, p1_serve, p2_serve)
    return p1_wins_game * p_set_if_win + (1 - p1_wins_game) * p_set_if_lose


def p_win_set(server: int, p1_serve: float, p2_serve: float) -> float:
    """P(player1 wins set) from 0-0."""
    return _p_win_set_from(0, 0, server, p1_serve, p2_serve)


# ---------------------------------------------------------------------------
# Match-level (Bo3) probability
# ---------------------------------------------------------------------------

@lru_cache(maxsize=8192)
def _p_win_match_from(
    p1_sets: int, p2_sets: int,
    p1_games: int, p2_games: int,
    p1_pts: int, p2_pts: int,
    server: int,
    p1_serve: float, p2_serve: float,
    in_tiebreak: bool = False,
) -> float:
    """
    P(player1 wins Bo3 match) from arbitrary mid-match state.
    """
    # Match terminal
    if p1_sets == 2:
        return 1.0
    if p2_sets == 2:
        return 0.0

    # P(p1 wins current point)
    p_win_pt = p1_serve if server == 0 else (1.0 - p2_serve)

    if in_tiebreak:
        # Determine if tiebreak is over
        if p1_pts >= 7 and p1_pts - p2_pts >= 2:
            # P1 won set
            return _p_win_match_from(
                p1_sets + 1, p2_sets, 0, 0, 0, 0, 1 - server,
                p1_serve, p2_serve, False
            )
        if p2_pts >= 7 and p2_pts - p1_pts >= 2:
            return _p_win_match_from(
                p1_sets, p2_sets + 1, 0, 0, 0, 0, 1 - server,
                p1_serve, p2_serve, False
            )
        total = p1_pts + p2_pts
        next_server_tb = (1 - server) if (total >= 12 or total % 2 == 1) else server
        pw = _p_win_match_from(
            p1_sets, p2_sets, p1_games, p2_games,
            p1_pts + 1, p2_pts, next_server_tb, p1_serve, p2_serve, True
        )
        pl = _p_win_match_from(
            p1_sets, p2_sets, p1_games, p2_games,
            p1_pts, p2_pts + 1, next_server_tb, p1_serve, p2_serve, True
        )
        return p_win_pt * pw + (1 - p_win_pt) * pl

    # Regular game: p1_pts/p2_pts are game points (0-3, deuce at 3-3)
    sp = min(p1_pts if server == 0 else p2_pts, 3)
    rp = min(p2_pts if server == 0 else p1_pts, 3)

    if sp == 4:
        # Current server won game
        if server == 0:
            new_p1g, new_p2g = p1_games + 1, p2_games
        else:
            new_p1g, new_p2g = p1_games, p2_games + 1
        new_server = 1 - server
        # Check set end
        return _resolve_game_won(
            p1_sets, p2_sets, new_p1g, new_p2g, new_server, p1_serve, p2_serve, server == 0
        )
    if rp == 4:
        # Returner won game
        if server == 0:
            new_p1g, new_p2g = p1_games, p2_games + 1
        else:
            new_p1g, new_p2g = p1_games + 1, p2_games
        new_server = 1 - server
        return _resolve_game_won(
            p1_sets, p2_sets, new_p1g, new_p2g, new_server, p1_serve, p2_serve, server != 0
        )

    # Normal game continuation
    if server == 0:
        new_p1p_w, new_p2p_w = min(p1_pts + 1, 4), p2_pts
        new_p1p_l, new_p2p_l = p1_pts, min(p2_pts + 1, 4)
    else:
        new_p1p_w, new_p2p_w = p1_pts, min(p2_pts + 1, 4)
        new_p1p_l, new_p2p_l = min(p1_pts + 1, 4), p2_pts

    # Deuce handling: if both reach 3, stay at (3,3) until advantage
    if new_p1p_w == 3 and new_p2p_w == 3:
        new_p1p_w, new_p2p_w = 3, 3
    if new_p1p_l == 3 and new_p2p_l == 3:
        new_p1p_l, new_p2p_l = 3, 3

    pw = _p_win_match_from(
        p1_sets, p2_sets, p1_games, p2_games,
        new_p1p_w, new_p2p_w, server, p1_serve, p2_serve, False
    )
    pl = _p_win_match_from(
        p1_sets, p2_sets, p1_games, p2_games,
        new_p1p_l, new_p2p_l, server, p1_serve, p2_serve, False
    )
    return p_win_pt * pw + (1 - p_win_pt) * pl


def _resolve_game_won(
    p1_sets: int, p2_sets: int,
    p1_games: int, p2_games: int,
    new_server: int,
    p1_serve: float, p2_serve: float,
    p1_won_game: bool,
) -> float:
    """Handle set/match completion after a game ends."""
    # Check set win conditions
    p1_set_won = (
        (p1_games >= 6 and p1_games - p2_games >= 2) or p1_games == 7
    )
    p2_set_won = (
        (p2_games >= 6 and p2_games - p1_games >= 2) or p2_games == 7
    )

    if p1_set_won:
        return _p_win_match_from(
            p1_sets + 1, p2_sets, 0, 0, 0, 0, new_server, p1_serve, p2_serve, False
        )
    if p2_set_won:
        return _p_win_match_from(
            p1_sets, p2_sets + 1, 0, 0, 0, 0, new_server, p1_serve, p2_serve, False
        )

    # Tiebreak?
    if p1_games == 6 and p2_games == 6:
        return _p_win_match_from(
            p1_sets, p2_sets, p1_games, p2_games, 0, 0, new_server, p1_serve, p2_serve, True
        )

    return _p_win_match_from(
        p1_sets, p2_sets, p1_games, p2_games, 0, 0, new_server, p1_serve, p2_serve, False
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def compute_win_probability(
    p1_serve: float,
    p2_serve: float,
    p1_sets: int = 0,
    p2_sets: int = 0,
    p1_games: int = 0,
    p2_games: int = 0,
    p1_pts: int = 0,
    p2_pts: int = 0,
    server: int = 0,
    in_tiebreak: bool = False,
) -> float:
    """
    P(player1 wins Bo3 match) from arbitrary mid-match state.

    Args:
        p1_serve: P(p1 wins a point) when p1 is serving
        p2_serve: P(p2 wins a point) when p2 is serving
        p1_sets/p2_sets: sets won (0-1 in Bo3)
        p1_games/p2_games: games won in current set
        p1_pts/p2_pts: points in current game (0-3, deuce handled internally)
        server: 0=p1 serving, 1=p2 serving
        in_tiebreak: True if currently in a tiebreak
    Returns:
        float in [0, 1]: P(player1 wins match)
    """
    _p_win_match_from.cache_clear()
    _p_win_tiebreak_from.cache_clear()
    _p_win_set_from.cache_clear()
    _p_win_game_from.cache_clear()
    return _p_win_match_from(
        p1_sets, p2_sets, p1_games, p2_games,
        p1_pts, p2_pts, server, p1_serve, p2_serve, in_tiebreak
    )


# ---------------------------------------------------------------------------
# ELO → serve probabilities via numerical calibration
# ---------------------------------------------------------------------------

def elo_to_serve_probs(
    elo1: float, elo2: float,
    tour: str, surface: str,
) -> tuple[float, float]:
    """
    Derive per-point serve win probabilities from ELO ratings.
    Uses bisection to find p1_adj such that Markov match win matches ELO logistic.
    Returns (p1_serve_win, p2_serve_win).
    """
    from app.engine.tables import baseline_serve_pt_win, surface_adj_hold

    base = baseline_serve_pt_win(tour, surface)  # type: ignore[arg-type]
    elo_gap = elo1 - elo2
    p_match_target = 1.0 / (1.0 + 10 ** (-elo_gap / 400.0))

    # Bisection: find δ such that compute_win_probability(base+δ, base-δ) ≈ p_match_target
    lo, hi = -0.20, 0.20

    def _match_p(delta: float) -> float:
        _p_win_match_from.cache_clear()
        _p_win_set_from.cache_clear()
        _p_win_game_from.cache_clear()
        _p_win_tiebreak_from.cache_clear()
        p1s = max(0.30, min(0.85, base + delta))
        p2s = max(0.30, min(0.85, base - delta))
        return compute_win_probability(p1s, p2s)

    for _ in range(40):
        mid = (lo + hi) / 2
        if _match_p(mid) < p_match_target:
            lo = mid
        else:
            hi = mid

    delta = (lo + hi) / 2
    p1s = max(0.30, min(0.85, base + delta))
    p2s = max(0.30, min(0.85, base - delta))
    return p1s, p2s
