"""
Live tennis scores via Sofascore public API (primary) with ESPN fallback.

Sofascore covers ALL ATP/WTA events including 250/500/Masters/Slams.
ESPN covers only Grand Slams and select Davis Cup events.

Live endpoint  : https://api.sofascore.com/api/v1/sport/tennis/events/live
Today endpoint : https://api.sofascore.com/api/v1/sport/tennis/scheduled-events/{YYYY-MM-DD}
"""
from __future__ import annotations
import datetime
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ─── Sofascore ──────────────────────────────────────────────────────────────

_SF_LIVE  = "https://api.sofascore.com/api/v1/sport/tennis/events/live"
_SF_TODAY = "https://api.sofascore.com/api/v1/sport/tennis/scheduled-events/{date}"

_SF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://www.sofascore.com/",
}

# ─── ESPN (fallback) ─────────────────────────────────────────────────────────

_ESPN_ATP_BASE = "https://site.api.espn.com/apis/site/v2/sports/tennis/atp/scoreboard"
_ESPN_WTA_BASE = "https://site.api.espn.com/apis/site/v2/sports/tennis/wta/scoreboard"
ESPN_ATP = _ESPN_ATP_BASE  # kept for backward compat
ESPN_WTA = _ESPN_WTA_BASE

_ESPN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TennisArbBot/1.0)",
    "Accept": "application/json",
}

_ESPN_STATUS = {
    "STATUS_IN_PROGRESS": "live",
    "STATUS_HALFTIME":    "live",
    "STATUS_DELAYED":     "live",
    "STATUS_SCHEDULED":   "scheduled",
    "STATUS_FINAL":       "finished",
    "STATUS_FINAL_OT":    "finished",
}

_POINT_MAP = {
    "0": 0, "15": 1, "30": 2, "40": 3,
    "AD": 4, "A": 4, "ADV": 4, "ADVANTAGE": 4, "DEUCE": 3,
}


# ════════════════════════════════════════════════════════════════════════════
# Sofascore helpers
# ════════════════════════════════════════════════════════════════════════════

def _sf_tour(event: dict) -> str:
    cat = (
        event.get("tournament", {})
        .get("uniqueTournament", {})
        .get("category", {})
        .get("name", "")
    ).upper()
    if "WTA" in cat or "WOMEN" in cat:
        return "WTA"
    return "ATP"


def _sf_surface(event: dict) -> str:
    ground = (
        event.get("groundType")
        or event.get("tournament", {}).get("groundType")
        or event.get("tournament", {}).get("uniqueTournament", {}).get("groundType")
        or ""
    ).lower()
    if "clay" in ground:
        return "clay"
    if "grass" in ground:
        return "grass"
    return "hard"


def _sf_is_set_done(g1: int, g2: int) -> bool:
    if (g1 >= 6 and g1 - g2 >= 2) or (g2 >= 6 and g2 - g1 >= 2):
        return True
    return g1 == 7 or g2 == 7


def _parse_sf_event(event: dict) -> Optional[dict]:
    """Convert one Sofascore event dict to our normalised match format."""
    status_type = (event.get("status") or {}).get("type", "notstarted")
    status_map = {
        "inprogress": "live",
        "finished":   "finished",
        "notstarted": "scheduled",
        "postponed":  "postponed",
        "canceled":   "cancelled",
    }
    status = status_map.get(status_type, "scheduled")

    home = event.get("homeTeam") or {}
    away = event.get("awayTeam") or {}
    p1_name = home.get("name") or home.get("shortName") or "Unknown"
    p2_name = away.get("name") or away.get("shortName") or "Unknown"

    hs = event.get("homeScore") or {}
    as_ = event.get("awayScore") or {}

    p1_sets = p2_sets = 0
    p1_games = p2_games = 0
    score_parts: list[str] = []

    for i in range(1, 6):
        key = f"period{i}"
        g1 = hs.get(key)
        g2 = as_.get(key)
        if g1 is None and g2 is None:
            break
        g1 = int(g1 or 0)
        g2 = int(g2 or 0)
        score_parts.append(f"{g1}-{g2}")
        if status == "finished" or _sf_is_set_done(g1, g2):
            if g1 > g2:
                p1_sets += 1
            else:
                p2_sets += 1
        else:
            p1_games = g1
            p2_games = g2

    server = int(event.get("serveIndex") or 0)
    in_tiebreak = bool(event.get("tieBreak") or False)

    tournament = (
        event.get("tournament", {}).get("name")
        or event.get("tournament", {})
        .get("uniqueTournament", {})
        .get("name")
        or ""
    )
    round_info = str((event.get("roundInfo") or {}).get("round", ""))

    return {
        "external_id":   f"sf_{event.get('id', '')}",
        "player1_name":  p1_name,
        "player2_name":  p2_name,
        "tour":          _sf_tour(event),
        "surface":       _sf_surface(event),
        "tournament":    tournament,
        "round":         round_info,
        "status":        status,
        "p1_sets":       p1_sets,
        "p2_sets":       p2_sets,
        "p1_games":      p1_games,
        "p2_games":      p2_games,
        "p1_pts":        0,
        "p2_pts":        0,
        "server":        server,
        "in_tiebreak":   in_tiebreak,
        "score_text":    ", ".join(score_parts),
    }


async def _fetch_sofascore(url: str) -> list[dict]:
    """Fetch and parse Sofascore events."""
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers=_SF_HEADERS)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning(f"Sofascore fetch failed ({url}): {exc}")
        return []

    events = data.get("events") or []
    results: list[dict] = []
    for ev in events:
        try:
            parsed = _parse_sf_event(ev)
            if parsed:
                results.append(parsed)
        except Exception as exc:
            logger.debug(f"Failed to parse Sofascore event {ev.get('id')}: {exc}")

    return results


# ════════════════════════════════════════════════════════════════════════════
# ESPN helpers (fallback)
# ════════════════════════════════════════════════════════════════════════════

def _surface_normalize(s: str) -> str:
    s = (s or "").lower()
    if "clay" in s:
        return "clay"
    if "grass" in s or "carpet" in s:
        return "grass"
    return "hard"


def _safe_int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _parse_espn_event(event: dict, tour: str) -> Optional[dict]:
    competitions = event.get("competitions") or []
    if not competitions:
        return None
    comp = competitions[0]

    status_obj = comp.get("status") or event.get("status") or {}
    status_type = (status_obj.get("type") or {}).get("name", "STATUS_SCHEDULED")
    status = _ESPN_STATUS.get(status_type, "scheduled")

    competitors = comp.get("competitors") or []
    if len(competitors) < 2:
        return None
    c1, c2 = competitors[0], competitors[1]

    def get_name(c: dict) -> str:
        ath = c.get("athlete") or c.get("team") or {}
        return (
            ath.get("displayName") or ath.get("fullName")
            or c.get("displayName") or "Unknown"
        )

    p1_ls = [ls.get("value", 0) for ls in (c1.get("linescores") or [])]
    p2_ls = [ls.get("value", 0) for ls in (c2.get("linescores") or [])]

    p1_sets = p2_sets = p1_games = p2_games = 0
    for i in range(max(len(p1_ls), len(p2_ls))):
        g1 = _safe_int(p1_ls[i] if i < len(p1_ls) else 0)
        g2 = _safe_int(p2_ls[i] if i < len(p2_ls) else 0)
        done = (status == "finished") or ((g1 >= 6 or g2 >= 6) and abs(g1 - g2) >= 2) or g1 == 7 or g2 == 7
        if done:
            if g1 > g2:
                p1_sets += 1
            else:
                p2_sets += 1
        else:
            p1_games = g1
            p2_games = g2

    situation = comp.get("situation") or {}
    serving_id = situation.get("servingAthleteId") or situation.get("serverId")
    server = 0
    if serving_id:
        c1_id = str((c1.get("athlete") or c1).get("id", ""))
        server = 0 if str(serving_id) == c1_id else 1

    venue = event.get("venue") or {}
    surface_str = venue.get("surface") or ""
    surface = _surface_normalize(surface_str)

    score_parts = []
    for i in range(max(len(p1_ls), len(p2_ls))):
        g1 = _safe_int(p1_ls[i] if i < len(p1_ls) else 0)
        g2 = _safe_int(p2_ls[i] if i < len(p2_ls) else 0)
        score_parts.append(f"{g1}-{g2}")

    return {
        "external_id":  f"espn_{tour}_{event.get('id', '')}",
        "player1_name": get_name(c1),
        "player2_name": get_name(c2),
        "tour":         tour,
        "surface":      surface,
        "tournament":   event.get("name") or event.get("shortName") or "",
        "round":        event.get("shortName", ""),
        "status":       status,
        "p1_sets":      p1_sets,
        "p2_sets":      p2_sets,
        "p1_games":     p1_games,
        "p2_games":     p2_games,
        "p1_pts":       0,
        "p2_pts":       0,
        "server":       server,
        "in_tiebreak":  False,
        "score_text":   ", ".join(score_parts),
    }


async def _fetch_espn(url: str, tour: str) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            resp = await client.get(url, headers=_ESPN_HEADERS)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning(f"ESPN fetch failed ({url}): {exc}")
        return []

    events = data.get("events") or []
    if not events:
        logger.info(f"ESPN {tour}: 0 events from {url}")
    else:
        statuses = list({
            (e.get("status") or {}).get("type", {}).get("name", "?")
            for e in events
        })
        logger.info(f"ESPN {tour}: {len(events)} events, statuses={statuses}")

    results: list[dict] = []
    for ev in events:
        try:
            parsed = _parse_espn_event(ev, tour)
            if parsed:
                results.append(parsed)
        except Exception as exc:
            logger.warning(f"Failed to parse ESPN event {ev.get('id')}: {exc}")

    return results


# ════════════════════════════════════════════════════════════════════════════
# Public API — called by jobs and handlers
# ════════════════════════════════════════════════════════════════════════════

async def fetch_live_matches() -> list[dict]:
    """
    Fetch all currently live ATP and WTA matches.
    Primary: Sofascore /events/live (covers all tours).
    Fallback: ESPN scoreboard (Grand Slams only).
    """
    # --- Sofascore primary ---
    sf_all = await _fetch_sofascore(_SF_LIVE)
    sf_live = [m for m in sf_all if m["status"] == "live"]
    if sf_all:
        logger.info(
            f"Sofascore live: {len(sf_live)} live / {len(sf_all)} total"
        )
        return sf_live

    # --- ESPN fallback ---
    import asyncio
    today = datetime.date.today().strftime("%Y%m%d")
    atp, wta = await asyncio.gather(
        _fetch_espn(f"{_ESPN_ATP_BASE}?dates={today}", "ATP"),
        _fetch_espn(f"{_ESPN_WTA_BASE}?dates={today}", "WTA"),
    )
    all_matches = atp + wta
    live = [m for m in all_matches if m["status"] == "live"]
    logger.info(
        f"ESPN fallback: {len(live)} live / {len(all_matches)} total"
    )
    return live


async def fetch_all_today() -> list[dict]:
    """Fetch ALL of today's matches (any status) — used by /refresh command."""
    today = datetime.date.today().strftime("%Y-%m-%d")
    sf_today_url = _SF_TODAY.format(date=today)
    all_matches = await _fetch_sofascore(sf_today_url)
    if all_matches:
        logger.info(f"Sofascore today: {len(all_matches)} total matches")
        return all_matches

    # ESPN fallback
    import asyncio
    today_espn = datetime.date.today().strftime("%Y%m%d")
    atp, wta = await asyncio.gather(
        _fetch_espn(f"{_ESPN_ATP_BASE}?dates={today_espn}", "ATP"),
        _fetch_espn(f"{_ESPN_WTA_BASE}?dates={today_espn}", "WTA"),
    )
    result = atp + wta
    logger.info(f"ESPN today fallback: {len(result)} total matches")
    return result


async def fetch_upcoming_matches() -> list[dict]:
    """Fetch today's scheduled/live matches (for pre-loading players)."""
    all_today = await fetch_all_today()
    return [m for m in all_today if m["status"] in ("scheduled", "live")]
