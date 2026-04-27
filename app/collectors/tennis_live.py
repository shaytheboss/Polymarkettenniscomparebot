"""
Live tennis scores via ESPN public (no-key) API.
Endpoints:
  ATP: https://site.api.espn.com/apis/site/v2/sports/tennis/atp/scoreboard
  WTA: https://site.api.espn.com/apis/site/v2/sports/tennis/wta/scoreboard

ESPN returns set-level scores and match status.
Point-level data (current game score) is parsed from statusClock / situation
fields where available; otherwise defaults to 0-0.
"""
from __future__ import annotations
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

ESPN_ATP = "https://site.api.espn.com/apis/site/v2/sports/tennis/atp/scoreboard"
ESPN_WTA = "https://site.api.espn.com/apis/site/v2/sports/tennis/wta/scoreboard"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; TennisArbBot/1.0)"
    ),
    "Accept": "application/json",
}

# ESPN status name → our status
_STATUS_MAP = {
    "STATUS_IN_PROGRESS": "live",
    "STATUS_HALFTIME": "live",
    "STATUS_DELAYED": "live",
    "STATUS_SCHEDULED": "scheduled",
    "STATUS_FINAL": "finished",
    "STATUS_FINAL_OT": "finished",
    "STATUS_POSTPONED": "postponed",
    "STATUS_CANCELED": "cancelled",
}

# Mapping ESPN score representations to integer point values
_POINT_MAP = {
    "0": 0, "15": 1, "30": 2, "40": 3, "AD": 4, "A": 4,
    "ADV": 4, "ADVANTAGE": 4, "DEUCE": 3,
}


def _surface_normalize(s: str) -> str:
    s = (s or "").lower()
    if "clay" in s:
        return "clay"
    if "grass" in s or "carpet" in s or "wimbledon" in s:
        return "grass"
    return "hard"


def _parse_point_score(raw: str) -> int:
    """Convert '0','15','30','40','AD' to 0-4."""
    return _POINT_MAP.get(str(raw).upper().strip(), 0)


def _parse_game_score(situation: Optional[dict]) -> tuple[int, int, int, bool]:
    """
    Parse current game/point score from ESPN 'situation' block.
    Returns (p1_pts, p2_pts, server, in_tiebreak).
    server: 0=p1, 1=p2.
    """
    if not situation:
        return 0, 0, 0, False

    # ESPN puts game score as strings like "30-15", "AD-40", "0-0"
    game_str = situation.get("shortDisplayName", "") or situation.get("displayClock", "")
    p1_pts = p2_pts = 0
    in_tb = False

    if "-" in game_str:
        parts = game_str.split("-")
        if len(parts) == 2:
            p1_pts = _parse_point_score(parts[0].strip())
            p2_pts = _parse_point_score(parts[1].strip())

    # Tiebreak detection
    tb_flag = situation.get("isTiebreak") or situation.get("tiebreak")
    if isinstance(tb_flag, bool):
        in_tb = tb_flag
    elif isinstance(tb_flag, str):
        in_tb = tb_flag.lower() in ("true", "1", "yes")

    # Server: ESPN uses "serving" boolean on competitor or "serves" field
    server_id = situation.get("servingAthleteId") or situation.get("serverId")
    server = 0  # default p1 serving; resolved later in _parse_espn_event

    return p1_pts, p2_pts, server, in_tb


def _parse_espn_event(event: dict, tour: str) -> Optional[dict]:
    """Convert a single ESPN event dict to our normalized match format."""
    competitions = event.get("competitions") or []
    if not competitions:
        return None
    comp = competitions[0]

    # Status
    status_obj = comp.get("status") or event.get("status") or {}
    status_type = (status_obj.get("type") or {}).get("name", "STATUS_SCHEDULED")
    status = _STATUS_MAP.get(status_type, "scheduled")

    # Players
    competitors = comp.get("competitors") or []
    if len(competitors) < 2:
        return None

    # ESPN: home=0, away=1 (but it's not always consistent for tennis)
    # We take them in order: first competitor = "player1", second = "player2"
    c1, c2 = competitors[0], competitors[1]

    def get_name(c: dict) -> str:
        ath = c.get("athlete") or c.get("team") or {}
        return (
            ath.get("displayName")
            or ath.get("fullName")
            or ath.get("shortName")
            or c.get("displayName")
            or "Unknown"
        )

    p1_name = get_name(c1)
    p2_name = get_name(c2)

    def _safe_int(v) -> int:
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0

    # Set scores from linescores
    p1_linescores = [ls.get("value", 0) for ls in (c1.get("linescores") or [])]
    p2_linescores = [ls.get("value", 0) for ls in (c2.get("linescores") or [])]

    # Count completed sets
    p1_sets = p2_sets = 0
    p1_games_current = p2_games_current = 0

    num_sets = max(len(p1_linescores), len(p2_linescores))
    for i in range(num_sets):
        g1 = _safe_int(p1_linescores[i]) if i < len(p1_linescores) else 0
        g2 = _safe_int(p2_linescores[i]) if i < len(p2_linescores) else 0
        # Determine if this set is finished or ongoing
        set_done = _is_set_complete(g1, g2, status)
        if set_done:
            if g1 > g2:
                p1_sets += 1
            else:
                p2_sets += 1
        else:
            # Current set in progress
            p1_games_current = g1
            p2_games_current = g2

    # Current game score from situation
    situation = comp.get("situation") or {}
    p1_pts, p2_pts, _, in_tb = _parse_game_score(situation)

    # Server detection: ESPN marks the serving player
    serving_id = (
        situation.get("servingAthleteId")
        or situation.get("serverId")
    )
    server = 0  # default
    if serving_id:
        c1_id = str((c1.get("athlete") or c1).get("id", ""))
        server = 0 if str(serving_id) == c1_id else 1

    # Surface from venue or notes
    venue = (event.get("venue") or {})
    surface_str = (
        venue.get("surface")
        or event.get("notes", [{}])[0].get("headline", "") if event.get("notes") else ""
    )
    surface = _surface_normalize(surface_str)

    # Tournament name
    season = event.get("season") or {}
    tournament = (
        event.get("name")
        or event.get("shortName")
        or season.get("slug", "")
        or ""
    )
    # Strip player names from tournament name if ESPN includes them
    for pn in [p1_name.split()[-1], p2_name.split()[-1]]:
        tournament = tournament.replace(pn, "").strip(" vs.,")

    # Score text
    score_parts = []
    for i in range(max(len(p1_linescores), len(p2_linescores))):
        g1 = _safe_int(p1_linescores[i]) if i < len(p1_linescores) else 0
        g2 = _safe_int(p2_linescores[i]) if i < len(p2_linescores) else 0
        score_parts.append(f"{g1}-{g2}")
    score_text = ", ".join(score_parts)

    return {
        "external_id": f"espn_{tour}_{event.get('id', '')}",
        "player1_name": p1_name,
        "player2_name": p2_name,
        "tour": tour,
        "surface": surface,
        "tournament": tournament,
        "round": event.get("shortName", ""),
        "status": status,
        "p1_sets": p1_sets,
        "p2_sets": p2_sets,
        "p1_games": p1_games_current,
        "p2_games": p2_games_current,
        "p1_pts": p1_pts,
        "p2_pts": p2_pts,
        "server": server,
        "in_tiebreak": in_tb,
        "score_text": score_text,
        "raw": {
            "event_id": event.get("id"),
            "status_name": status_type,
            "p1_linescores": p1_linescores,
            "p2_linescores": p2_linescores,
        },
    }


def _is_set_complete(g1: int, g2: int, match_status: str) -> bool:
    """Heuristic: a set is complete if one player won it by standard rules."""
    if match_status == "finished":
        return True
    # Standard set: one player at 6+ and leads by 2, or one at 7
    if (g1 >= 6 and g1 - g2 >= 2) or (g2 >= 6 and g2 - g1 >= 2):
        return True
    if g1 == 7 or g2 == 7:
        return True
    return False


async def _fetch_espn(url: str, tour: str) -> list[dict]:
    """Fetch and parse ESPN scoreboard for one tour."""
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            resp = await client.get(url, headers=_HEADERS)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error(f"ESPN fetch failed ({url}): {e}")
        return []

    events = data.get("events") or []
    results = []
    for event in events:
        try:
            parsed = _parse_espn_event(event, tour)
            if parsed:
                results.append(parsed)
        except Exception as e:
            logger.debug(f"Failed to parse ESPN event {event.get('id')}: {e}")

    return results


async def fetch_live_matches() -> list[dict]:
    """
    Fetch all currently live ATP and WTA matches from ESPN.
    Returns normalized match dicts.
    """
    import asyncio
    atp, wta = await asyncio.gather(
        _fetch_espn(ESPN_ATP, "ATP"),
        _fetch_espn(ESPN_WTA, "WTA"),
    )
    all_matches = atp + wta
    live = [m for m in all_matches if m["status"] == "live"]
    logger.info(f"ESPN: {len(live)} live matches ({len(atp)} ATP, {len(wta)} WTA fetched)")
    return live


async def fetch_upcoming_matches() -> list[dict]:
    """Fetch today's scheduled matches (for pre-loading players)."""
    import asyncio
    atp, wta = await asyncio.gather(
        _fetch_espn(ESPN_ATP, "ATP"),
        _fetch_espn(ESPN_WTA, "WTA"),
    )
    return [m for m in (atp + wta) if m["status"] in ("scheduled", "live")]
