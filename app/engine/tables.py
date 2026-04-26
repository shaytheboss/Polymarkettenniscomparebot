"""
Reference probability tables encoded from empirical research and Markov models.
[H]=Empirical large dataset, [M]=Markov/Elo model, [L]=Low-confidence
Sources: Sackmann repos, O'Shannessy (Infosys), Klaassen-Magnus 2001,
         TennisRatio, Fitzpatrick et al. 2019, Buszard et al. 2024
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

Tour    = Literal["ATP", "WTA"]
Surface = Literal["hard", "clay", "grass"]
EloBand = Literal["E0", "E1", "E2", "E3"]


# ---------------------------------------------------------------------------
# A. ELO GAP → ELO BAND
# ---------------------------------------------------------------------------
def elo_band(elo_gap: float) -> EloBand:
    """Classify absolute ELO gap into research band."""
    g = abs(elo_gap)
    if g < 50:
        return "E0"
    if g < 150:
        return "E1"
    if g < 300:
        return "E2"
    return "E3"


# ---------------------------------------------------------------------------
# B. PRE-MATCH WIN PROBABILITY FROM ELO  (logistic, hard-court reference)
# ---------------------------------------------------------------------------
PRE_MATCH_ELO_TABLE: dict[EloBand, dict[Tour, tuple[float, float]]] = {
    # band -> tour -> (center, half_width)  → range = center ± half_width
    "E0": {"ATP": (0.55, 0.02), "WTA": (0.54, 0.02)},
    "E1": {"ATP": (0.635, 0.035), "WTA": (0.625, 0.035)},
    "E2": {"ATP": (0.74, 0.06), "WTA": (0.72, 0.06)},
    "E3": {"ATP": (0.875, 0.075), "WTA": (0.84, 0.08)},
}


def pre_match_prob_from_elo(elo_gap: float, tour: Tour) -> float:
    """
    P(higher-ELO player wins match) from logistic formula.
    elo_gap = elo_fav - elo_underdog  (positive means fav has higher ELO)
    """
    import math
    p = 1.0 / (1.0 + 10 ** (-elo_gap / 400.0))
    # Clip to empirically observed range
    band = elo_band(elo_gap)
    center, hw = PRE_MATCH_ELO_TABLE[band][tour]
    lo, hi = center - hw, center + hw
    return max(lo, min(hi, p))


# ---------------------------------------------------------------------------
# C. P(WIN MATCH | SET 1 RESULT) — Table B + C from reference
# ---------------------------------------------------------------------------
_LOST_S1: dict[EloBand, dict[Tour, float]] = {
    "E0": {"ATP": 0.35, "WTA": 0.38},
    "E1": {"ATP": 0.40, "WTA": 0.42},
    "E2": {"ATP": 0.47, "WTA": 0.49},
    "E3": {"ATP": 0.55, "WTA": 0.57},
}

_WON_S1: dict[EloBand, dict[Tour, float]] = {
    "E0": {"ATP": 0.73, "WTA": 0.71},
    "E1": {"ATP": 0.82, "WTA": 0.80},
    "E2": {"ATP": 0.88, "WTA": 0.87},
    "E3": {"ATP": 0.94, "WTA": 0.93},
}


def prob_after_set1(won_set1: bool, band: EloBand, tour: Tour) -> float:
    """P(favorite wins match) after set 1 result."""
    if won_set1:
        return _WON_S1[band][tour]
    return _LOST_S1[band][tour]


# ---------------------------------------------------------------------------
# D. MID-MATCH STATE TABLE  (hard-court reference)
# States: indexed 0-10 from reference document.
# Format: state_id -> {(tour, band): probability}
# ---------------------------------------------------------------------------
MID_MATCH_STATES: dict[int, dict] = {
    0:  {"desc": "Match start 0-0", "ATP_E1": 0.64, "ATP_E2": 0.74, "WTA_E1": 0.62, "WTA_E2": 0.72},
    1:  {"desc": "Up a set, 3-3 s2, on serve", "ATP_E1": 0.83, "ATP_E2": 0.90, "WTA_E1": 0.80, "WTA_E2": 0.88},
    2:  {"desc": "Up a set, broken (1-3 s2, opp serving)", "ATP_E1": 0.70, "ATP_E2": 0.80, "WTA_E1": 0.66, "WTA_E2": 0.77},
    3:  {"desc": "Down a set, broke back (1-1 s2 on serve)", "ATP_E1": 0.35, "ATP_E2": 0.26, "WTA_E1": 0.38, "WTA_E2": 0.29},
    4:  {"desc": "1-1 sets, 3-3 s3, on serve", "ATP_E1": 0.67, "ATP_E2": 0.77, "WTA_E1": 0.64, "WTA_E2": 0.74},
    5:  {"desc": "1-1 sets, 3-3 s3, returning", "ATP_E1": 0.58, "ATP_E2": 0.68, "WTA_E1": 0.55, "WTA_E2": 0.64},
    6:  {"desc": "Up a set AND a break (3-1 s2, serving)", "ATP_E1": 0.92, "ATP_E2": 0.96, "WTA_E1": 0.89, "WTA_E2": 0.95},
    7:  {"desc": "Down a set AND a break (1-3 s2, returning)", "ATP_E1": 0.15, "ATP_E2": 0.09, "WTA_E1": 0.18, "WTA_E2": 0.11},
    8:  {"desc": "On serve at 5-5 s3 (fav serving)", "ATP_E1": 0.62, "ATP_E2": 0.70, "WTA_E1": 0.59, "WTA_E2": 0.67},
    9:  {"desc": "Serving at 5-4 s3 (fav won s1)", "ATP_E1": 0.88, "ATP_E2": 0.94, "WTA_E1": 0.84, "WTA_E2": 0.92},
    10: {"desc": "1-1 sets, 0-0 start of s3", "ATP_E1": 0.57, "ATP_E2": 0.67, "WTA_E1": 0.55, "WTA_E2": 0.64},
}


def lookup_mid_match_state(state_id: int, tour: Tour, band: EloBand) -> float | None:
    """Return tabled probability for a known mid-match state (hard-court)."""
    row = MID_MATCH_STATES.get(state_id)
    if row is None:
        return None
    # Only E1 and E2 have separate columns; for E0/E3 interpolate
    key = f"{tour}_{band}"
    if key in row:
        return row[key]
    # Extrapolate for E0 (below E1) and E3 (above E2)
    e1 = row.get(f"{tour}_E1", 0.0)
    e2 = row.get(f"{tour}_E2", 0.0)
    if band == "E0":
        return max(0.01, e1 - abs(e2 - e1) * 0.3)
    if band == "E3":
        return min(0.99, e2 + abs(e2 - e1) * 0.4)
    return None


# ---------------------------------------------------------------------------
# E. HOLD RATES  (baseline + score-state)
# ---------------------------------------------------------------------------
HOLD_RATE_BASELINE: dict[str, dict[Surface, float]] = {
    "ATP": {"hard": 0.79, "clay": 0.76, "grass": 0.835},
    "WTA": {"hard": 0.63, "clay": 0.59, "grass": 0.65},
}

# P(server holds | current game score) — (server_pts, returner_pts) → (ATP, WTA)
# pts encoded as 0=0, 1=15, 2=30, 3=40, 4=A (advantage)
HOLD_BY_SCORE: dict[tuple[int, int], tuple[float, float]] = {
    (0, 0): (0.79, 0.63),
    (1, 0): (0.84, 0.70),
    (0, 1): (0.72, 0.55),
    (2, 0): (0.91, 0.78),
    (0, 2): (0.55, 0.40),
    (2, 2): (0.74, 0.63),   # 30-30
    (3, 3): (0.74, 0.63),   # 40-40 / deuce
    (1, 3): (0.22, 0.18),   # 15-40
    (2, 3): (0.37, 0.33),   # 30-40
    (0, 3): (0.17, 0.10),   # 0-40 triple BP
    (3, 1): (0.92, 0.82),   # 40-15
    (3, 2): (0.87, 0.75),   # 40-30
    (4, 3): (0.58, 0.50),   # Advantage server (after deuce)
    (3, 4): (0.42, 0.50),   # Advantage returner
}


def hold_prob_at_score(server_pts: int, returner_pts: int, tour: Tour) -> float:
    """P(server eventually holds | current game score)."""
    key = (server_pts, returner_pts)
    row = HOLD_BY_SCORE.get(key)
    if row is None:
        # fallback to baseline
        return HOLD_RATE_BASELINE[tour]["hard"]
    return row[0] if tour == "ATP" else row[1]


# ---------------------------------------------------------------------------
# F. SURFACE ADJUSTMENTS (deltas in percentage points, applied to hard baseline)
# ---------------------------------------------------------------------------
SURFACE_ADJ: dict[str, dict[Surface, float]] = {
    # key -> surface -> adjustment in pp
    "hold_atp":          {"hard": 0.0, "clay": -0.025, "grass": +0.03},
    "hold_wta":          {"hard": 0.0, "clay": -0.04,  "grass": +0.02},
    "comeback_s1":       {"hard": 0.0, "clay": +0.04,  "grass": -0.04},
    "fav_win_overall":   {"hard": 0.0, "clay": -0.025, "grass": +0.015},
}


def surface_adj_hold(tour: Tour, surface: Surface) -> float:
    key = "hold_atp" if tour == "ATP" else "hold_wta"
    return SURFACE_ADJ[key].get(surface, 0.0)


def surface_adj_comeback(surface: Surface) -> float:
    return SURFACE_ADJ["comeback_s1"].get(surface, 0.0)


def surface_adj_overall(surface: Surface) -> float:
    return SURFACE_ADJ["fav_win_overall"].get(surface, 0.0)


# ---------------------------------------------------------------------------
# G. SERVE POINT WIN RATES PER TOUR/SURFACE (baseline — equal players)
# ---------------------------------------------------------------------------
SERVE_PT_WIN_BASELINE: dict[str, dict[Surface, float]] = {
    "ATP": {"hard": 0.635, "clay": 0.595, "grass": 0.670},
    "WTA": {"hard": 0.545, "clay": 0.510, "grass": 0.558},
}


def baseline_serve_pt_win(tour: Tour, surface: Surface) -> float:
    return SERVE_PT_WIN_BASELINE[tour][surface]


# ---------------------------------------------------------------------------
# H. DOCUMENTED MARKET EDGES
# ---------------------------------------------------------------------------
@dataclass
class MarketEdge:
    name: str
    condition: str
    math_says: str
    market_does: str
    edge_direction: str
    caveat: str


KNOWN_EDGES = [
    MarketEdge(
        name="SET1_LOSS_HEAVY_FAV",
        condition="E2/E3 favorite has just lost Set 1",
        math_says="Fav still 45-55% to win match",
        market_does="Often swings to 30-40% for fav (overreaction)",
        edge_direction="Back the set-1-losing favorite",
        caveat="Check: genuine E2/E3 ELO gap, not just ranking. Grass reduces by 3-5pp. Injury risk?",
    ),
    MarketEdge(
        name="WTA_REBREAK_MOMENTUM",
        condition="WTA player just got broken; market prices next game",
        math_says="Re-break P ~36-40% = baseline break rate. Lift: ~0pp (Sackmann 2011)",
        market_does="May price break-back at 50-60% due to momentum narrative",
        edge_direction="Fade the re-break; sell broken player's next game at inflated price",
        caveat="Verify actual market price. ATP re-break is slightly BELOW baseline.",
    ),
    MarketEdge(
        name="SERVING_FOR_MATCH_CHOKE",
        condition="E2/E3 fav serving at 5-4 or 5-3 in set 3",
        math_says="Hold rate ~80-85%, unchanged vs baseline. No choke for heavy favs (Sackmann 2015)",
        market_does="May price hold at 60-70% due to pressure narrative",
        edge_direction="Back the heavy favorite to hold and close",
        caveat="Effect IS real (small) in E0 close matches. Confirm ELO genuinely one-sided.",
    ),
]
