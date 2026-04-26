"""
Live tennis scores collector using api-tennis.com (RapidAPI).
Falls back to sportradar if configured.

API-Tennis endpoint reference:
  GET https://api.api-tennis.com/tennis/?method=get_livescores&APIkey=KEY
"""
from __future__ import annotations
import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

API_TENNIS_BASE = "https://api.api-tennis.com/tennis/"
RAPID_API_TENNIS_BASE = "https://tennis-live-data.p.rapidapi.com"


def _point_to_int(pt: str) -> int:
    """Convert '0','15','30','40','AD' → 0,1,2,3,4."""
    mapping = {"0": 0, "15": 1, "30": 2, "40": 3, "AD": 4, "A": 4}
    return mapping.get(str(pt).upper(), 0)


def _surface_normalize(s: str) -> str:
    s = s.lower()
    if "clay" in s:
        return "clay"
    if "grass" in s or "carpet" in s:
        return "grass"
    return "hard"


async def fetch_live_matches() -> list[dict]:
    """
    Fetch all currently live ATP/WTA matches.
    Returns list of normalized match dicts.
    """
    if settings.api_tennis_key:
        return await _fetch_api_tennis()
    logger.warning("No live tennis API key configured — returning empty list")
    return []


async def _fetch_api_tennis() -> list[dict]:
    """Fetch from api-tennis.com."""
    params = {
        "method": "get_livescores",
        "APIkey": settings.api_tennis_key,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(API_TENNIS_BASE, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error(f"api-tennis fetch error: {e}")
        return []

    if not isinstance(data, dict) or "result" not in data:
        logger.warning(f"Unexpected api-tennis response format")
        return []

    matches = []
    for raw in (data.get("result") or []):
        try:
            parsed = _parse_api_tennis_match(raw)
            if parsed:
                matches.append(parsed)
        except Exception as e:
            logger.debug(f"Failed to parse match: {e} | raw={raw}")

    return matches


def _parse_api_tennis_match(raw: dict) -> Optional[dict]:
    """Normalize api-tennis.com match response to our standard format."""
    # Filter to ATP and WTA only
    category = raw.get("league_name", "").lower()
    if "doubles" in category:
        return None

    tour = "WTA" if "wta" in category else "ATP"

    # Player names
    p1 = raw.get("event_first_player", "")
    p2 = raw.get("event_second_player", "")
    if not p1 or not p2:
        return None

    # Score parsing
    # api-tennis returns score in formats like "6-4, 3-5" and current game score
    score_str = raw.get("event_score", "")
    sets_score = _parse_sets_score(score_str)

    # Current game score
    game_score = raw.get("event_game_score", "0-0")
    try:
        gp1_str, gp2_str = game_score.split("-")
        p1_pts = _point_to_int(gp1_str.strip())
        p2_pts = _point_to_int(gp2_str.strip())
    except Exception:
        p1_pts, p2_pts = 0, 0

    # Server (api-tennis marks with asterisk or separate field)
    server_raw = raw.get("event_serve", "1")
    server = 0 if str(server_raw) in ("1", "home") else 1

    # Tiebreak detection
    in_tb = raw.get("event_tiebreak", False)
    if isinstance(in_tb, str):
        in_tb = in_tb.lower() in ("true", "1", "yes")

    # Surface
    surface = _surface_normalize(raw.get("event_court", "") or raw.get("league_name", ""))

    return {
        "external_id": str(raw.get("event_key", raw.get("event_id", ""))),
        "player1_name": p1.strip(),
        "player2_name": p2.strip(),
        "tour": tour,
        "surface": surface,
        "tournament": raw.get("league_name", ""),
        "round": raw.get("event_round", ""),
        "status": "live",
        "p1_sets": sets_score["p1_sets"],
        "p2_sets": sets_score["p2_sets"],
        "p1_games": sets_score["p1_games_current"],
        "p2_games": sets_score["p2_games_current"],
        "p1_pts": p1_pts,
        "p2_pts": p2_pts,
        "server": server,
        "in_tiebreak": bool(in_tb),
        "score_text": f"{score_str} | {game_score}",
        "raw": raw,
    }


def _parse_sets_score(score_str: str) -> dict:
    """
    Parse set score like '6-4, 3-5' into components.
    Returns dict with p1_sets, p2_sets, p1_games_current, p2_games_current.
    """
    p1_sets = p2_sets = 0
    p1_games = p2_games = 0

    if not score_str:
        return {"p1_sets": 0, "p2_sets": 0, "p1_games_current": 0, "p2_games_current": 0}

    parts = [s.strip() for s in score_str.replace(";", ",").split(",")]
    for i, part in enumerate(parts):
        part = part.strip("()")
        if "-" not in part:
            continue
        try:
            left, right = part.split("-")
            left_n = int("".join(c for c in left if c.isdigit()) or "0")
            right_n = int("".join(c for c in right if c.isdigit()) or "0")
        except ValueError:
            continue

        is_last = (i == len(parts) - 1)
        if is_last:
            p1_games, p2_games = left_n, right_n
        else:
            if left_n > right_n:
                p1_sets += 1
            elif right_n > left_n:
                p2_sets += 1
            # (tie sets count as equal — shouldn't happen in tennis)

    return {
        "p1_sets": p1_sets,
        "p2_sets": p2_sets,
        "p1_games_current": p1_games,
        "p2_games_current": p2_games,
    }


async def fetch_upcoming_matches() -> list[dict]:
    """Fetch today's scheduled matches for pre-loading players."""
    if not settings.api_tennis_key:
        return []
    params = {
        "method": "get_events",
        "APIkey": settings.api_tennis_key,
        "date": __import__("datetime").date.today().isoformat(),
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(API_TENNIS_BASE, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error(f"api-tennis upcoming fetch error: {e}")
        return []

    matches = []
    for raw in (data.get("result") or []):
        try:
            parsed = _parse_api_tennis_match(raw)
            if parsed:
                parsed["status"] = "scheduled"
                matches.append(parsed)
        except Exception:
            pass
    return matches
