"""
Live tennis scores — three-tier source cascade:
  1. Sofascore  (best coverage, but 403 on cloud IPs)
  2. ESPN       (Grand Slams + some Masters; returns tournament containers
                 with groupings → need to drill into groupings[].events[])
  3. TheSportsDB (free public API, key=1, cloud-accessible fallback)

ESPN scoreboard response structure (discovered from live logs):
  data["events"]               → list of TOURNAMENT containers
  tournament["groupings"]      → list of ROUNDS
  round["events"]              → list of individual MATCH events

ESPN match event formats (both handled):
  Format A (Grand Slams/direct): event["competitions"][0]["competitors"]
  Format B (groupings sub-events): event["competitors"] directly on event
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
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.6367.82 Mobile Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.sofascore.com",
    "Referer": "https://www.sofascore.com/",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}

# ─── ESPN ────────────────────────────────────────────────────────────────────

_ESPN_ATP_BASE = "https://site.api.espn.com/apis/site/v2/sports/tennis/atp/scoreboard"
_ESPN_WTA_BASE = "https://site.api.espn.com/apis/site/v2/sports/tennis/wta/scoreboard"
ESPN_ATP = _ESPN_ATP_BASE
ESPN_WTA = _ESPN_WTA_BASE

_ESPN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TennisArbBot/1.0)",
    "Accept": "application/json",
}

_ESPN_STATUS = {
    # Live states
    "STATUS_IN_PROGRESS": "live",
    "STATUS_HALFTIME":    "live",
    "STATUS_DELAYED":     "live",
    "STATUS_SUSPENDED":   "live",
    "STATUS_RAIN_DELAY":  "live",
    # Finished states (including retirement / walkover)
    "STATUS_FINAL":       "finished",
    "STATUS_FINAL_OT":    "finished",
    "STATUS_RETIRED":     "finished",
    "STATUS_WALKOVER":    "finished",
    "STATUS_ABANDONED":   "finished",
    # Scheduled / not-yet-started states
    "STATUS_SCHEDULED":   "scheduled",
    "STATUS_POSTPONED":   "scheduled",
    "STATUS_CANCELLED":   "scheduled",
    "STATUS_FORFEIT":     "finished",
}

_POINT_MAP = {
    "0": 0, "15": 1, "30": 2, "40": 3,
    "AD": 4, "A": 4, "ADV": 4, "ADVANTAGE": 4, "DEUCE": 3,
}

# ─── TheSportsDB (free, key=1, cloud-accessible) ─────────────────────────────

_TSDB_BASE = "https://www.thesportsdb.com/api/v1/json/1"
_TSDB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TennisArbBot/1.0)",
    "Accept": "application/json",
}

_TSDB_STATUS = {
    "Match Finished": "finished",
    "FT":             "finished",
    "In Progress":    "live",
    "Live":           "live",
    "NS":             "scheduled",
    "Not Started":    "scheduled",
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

    logger.info(f"Sofascore: {len(results)} matches from {url}")
    return results


# ════════════════════════════════════════════════════════════════════════════
# ESPN helpers
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


def _parse_espn_match(event: dict, tour: str, parent_venue: dict = None,
                      parent_tournament: str = "") -> Optional[dict]:
    """
    Parse a single ESPN match event. Handles two ESPN formats:
      Format A: event["competitions"][0]["competitors"]  (direct scoreboard / Grand Slams)
      Format B: event["competitors"] directly            (groupings sub-events — most common)
    """
    eid = event.get("id", "?")

    competitions = event.get("competitions") or []
    if competitions:
        # Format A — standard ESPN competition wrapper
        comp = competitions[0]
        status_obj = comp.get("status") or event.get("status") or {}
        competitors = comp.get("competitors") or []
        situation = comp.get("situation") or {}
        serving_id = situation.get("servingAthleteId") or situation.get("serverId")
        server = 0
        if serving_id and competitors:
            c1_id = str((competitors[0].get("athlete") or competitors[0]).get("id", ""))
            server = 0 if str(serving_id) == c1_id else 1
    else:
        # Format B — competitors directly on event (groupings sub-events)
        competitors = event.get("competitors") or []
        if not competitors:
            logger.debug(f"ESPN {tour} match {eid}: no competitors in either format, skipping")
            return None
        status_obj = event.get("status") or {}
        server = 0
        for i, c in enumerate(competitors):
            if c.get("serving"):
                server = i
                break

    status_type = (status_obj.get("type") or {}).get("name", "STATUS_SCHEDULED")
    if status_type not in _ESPN_STATUS:
        logger.warning(
            f"ESPN {tour} match {eid}: unknown status type '{status_type}' "
            f"— defaulting to 'scheduled'. Add to _ESPN_STATUS if this is a live state."
        )
    status = _ESPN_STATUS.get(status_type, "scheduled")

    if len(competitors) < 2:
        logger.debug(f"ESPN {tour} match {eid}: only {len(competitors)} competitor(s), skipping")
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
        done = (
            (status == "finished")
            or ((g1 >= 6 or g2 >= 6) and abs(g1 - g2) >= 2)
            or g1 == 7 or g2 == 7
        )
        if done:
            if g1 > g2:
                p1_sets += 1
            else:
                p2_sets += 1
        else:
            p1_games = g1
            p2_games = g2

    venue = event.get("venue") or parent_venue or {}
    surface_str = venue.get("surface") or ""
    surface = _surface_normalize(surface_str)

    score_parts = []
    for i in range(max(len(p1_ls), len(p2_ls))):
        g1 = _safe_int(p1_ls[i] if i < len(p1_ls) else 0)
        g2 = _safe_int(p2_ls[i] if i < len(p2_ls) else 0)
        score_parts.append(f"{g1}-{g2}")

    tournament = (
        event.get("name") or event.get("shortName")
        or parent_tournament or ""
    )

    p1_name = get_name(c1)
    p2_name = get_name(c2)
    logger.debug(
        f"ESPN {tour}: {p1_name} vs {p2_name} status={status} score={', '.join(score_parts) or 'n/a'}"
    )

    return {
        "external_id":  f"espn_{tour}_{eid}",
        "player1_name": p1_name,
        "player2_name": p2_name,
        "tour":         tour,
        "surface":      surface,
        "tournament":   tournament,
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


def _expand_espn_tournament(tournament: dict, tour: str) -> list[dict]:
    """
    ESPN scoreboard returns tournament containers (with 'groupings'), not
    individual matches. Drill in: tournament → groupings → events → matches.
    """
    tid = tournament.get("id", "?")
    tname = tournament.get("name") or tournament.get("shortName") or tid
    parent_venue = tournament.get("venue") or {}
    groupings = tournament.get("groupings") or []

    if not groupings:
        logger.debug(f"ESPN {tour} tournament '{tname}': has 0 groupings")
        return []

    logger.info(
        f"ESPN {tour} tournament '{tname}': {len(groupings)} grouping(s) — "
        f"round names={[g.get('name', g.get('shortName', '?')) for g in groupings]}"
    )

    matches = []
    for grp in groupings:
        grp_name = grp.get("name") or grp.get("shortName") or "?"
        grp_events = grp.get("events") or grp.get("competitions") or []

        if not grp_events:
            logger.info(f"  ESPN round '{grp_name}': 0 events (keys={list(grp.keys())})")
            continue

        logger.info(
            f"  ESPN round '{grp_name}': {len(grp_events)} event(s), "
            f"first_keys={list(grp_events[0].keys())[:12]}"
        )

        for ev in grp_events:
            try:
                parsed = _parse_espn_match(
                    ev, tour,
                    parent_venue=parent_venue,
                    parent_tournament=tname,
                )
                if parsed:
                    matches.append(parsed)
            except Exception as exc:
                logger.warning(
                    f"ESPN {tour} round '{grp_name}' event {ev.get('id','?')}: {exc}"
                )

    logger.info(
        f"ESPN {tour} tournament '{tname}': extracted {len(matches)} match(es)"
    )
    return matches


async def _fetch_espn(url: str, tour: str) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            resp = await client.get(url, headers=_ESPN_HEADERS)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning(f"ESPN fetch failed ({url}): {exc}")
        return []

    top_events = data.get("events") or []
    if not top_events:
        logger.info(f"ESPN {tour}: 0 top-level events from {url}")
        return []

    logger.info(
        f"ESPN {tour}: {len(top_events)} top-level item(s) from {url}; "
        f"first_keys={list(top_events[0].keys())[:10]}"
    )

    results: list[dict] = []
    for ev in top_events:
        try:
            if ev.get("competitions"):
                # Already a match event (direct format)
                parsed = _parse_espn_match(ev, tour)
                if parsed:
                    results.append(parsed)
            elif ev.get("groupings"):
                # Tournament container — drill into groupings
                expanded = _expand_espn_tournament(ev, tour)
                results.extend(expanded)
            else:
                logger.warning(
                    f"ESPN {tour} item {ev.get('id','?')}: "
                    f"neither 'competitions' nor 'groupings' — keys={list(ev.keys())}"
                )
        except Exception as exc:
            logger.warning(f"ESPN {tour} item {ev.get('id','?')} parse error: {exc}")

    logger.info(f"ESPN {tour}: total {len(results)} match(es) extracted")
    return results


# ════════════════════════════════════════════════════════════════════════════
# TheSportsDB helpers (free, cloud-accessible)
# ════════════════════════════════════════════════════════════════════════════

def _parse_tsdb_event(ev: dict) -> Optional[dict]:
    p1_name = ev.get("strHomeTeam") or "Unknown"
    p2_name = ev.get("strAwayTeam") or "Unknown"
    if p1_name == "Unknown" and p2_name == "Unknown":
        return None

    raw_status = ev.get("strStatus") or "NS"
    status = _TSDB_STATUS.get(raw_status, "scheduled")
    if raw_status not in _TSDB_STATUS:
        # Try partial match
        rl = raw_status.lower()
        if any(x in rl for x in ["finish", "ft", "ended"]):
            status = "finished"
        elif any(x in rl for x in ["progress", "live", "playing"]):
            status = "live"

    p1_score = _safe_int(ev.get("intHomeScore"))
    p2_score = _safe_int(ev.get("intAwayScore"))

    league = (ev.get("strLeague") or "").upper()
    tour = "WTA" if "WTA" in league or "WOMEN" in league else "ATP"

    venue = ev.get("strVenue") or ""
    surface_str = ev.get("strSeason") or ""  # TSDB doesn't always have surface
    surface = "hard"

    event_name = ev.get("strEvent") or f"{p1_name} vs {p2_name}"
    eid = ev.get("idEvent") or ""

    return {
        "external_id":  f"tsdb_{eid}",
        "player1_name": p1_name,
        "player2_name": p2_name,
        "tour":         tour,
        "surface":      surface,
        "tournament":   ev.get("strLeague") or "",
        "round":        ev.get("strRound") or "",
        "status":       status,
        "p1_sets":      p1_score,
        "p2_sets":      p2_score,
        "p1_games":     0,
        "p2_games":     0,
        "p1_pts":       0,
        "p2_pts":       0,
        "server":       0,
        "in_tiebreak":  False,
        "score_text":   f"{p1_score}-{p2_score}",
    }


async def _fetch_thesportsdb(date_str: str) -> list[dict]:
    """
    TheSportsDB free public API — returns tennis events for a given date.
    No API key needed for key=1 (public free tier).
    """
    url = f"{_TSDB_BASE}/eventsday.php?d={date_str}&s=Tennis"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers=_TSDB_HEADERS)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning(f"TheSportsDB fetch failed: {exc}")
        return []

    events = data.get("events") or []
    results = []
    for ev in events:
        try:
            parsed = _parse_tsdb_event(ev)
            if parsed:
                results.append(parsed)
        except Exception as exc:
            logger.debug(f"TheSportsDB parse error for {ev.get('idEvent')}: {exc}")

    logger.info(f"TheSportsDB: {len(results)} tennis matches for {date_str}")
    return results


# ════════════════════════════════════════════════════════════════════════════
# Public API — called by jobs and handlers
# ════════════════════════════════════════════════════════════════════════════

async def fetch_live_matches() -> list[dict]:
    """
    Fetch all currently live ATP and WTA matches.
    Primary: Sofascore /events/live.
    Fallback: ESPN scoreboard (handles groupings structure).
    """
    # --- Sofascore primary ---
    sf_all = await _fetch_sofascore(_SF_LIVE)
    sf_live = [m for m in sf_all if m["status"] == "live"]
    if sf_all:
        logger.info(f"Sofascore live: {len(sf_live)}/{len(sf_all)} live")
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
    logger.info(f"ESPN fallback live: {len(live)}/{len(all_matches)}")
    return live


async def fetch_all_today() -> list[dict]:
    """
    Fetch ALL of today's matches (any status) — used by job_fetch_live_scores.
    Three-tier cascade: Sofascore → ESPN → TheSportsDB.
    """
    today_sf  = datetime.date.today().strftime("%Y-%m-%d")
    today_espn = datetime.date.today().strftime("%Y%m%d")
    today_tsdb = today_sf

    # --- Tier 1: Sofascore ---
    sf_url = _SF_TODAY.format(date=today_sf)
    sf_matches = await _fetch_sofascore(sf_url)
    if sf_matches:
        logger.info(f"[Source: Sofascore] {len(sf_matches)} total matches today")
        return sf_matches

    # --- Tier 2: ESPN ---
    import asyncio
    atp, wta = await asyncio.gather(
        _fetch_espn(f"{_ESPN_ATP_BASE}?dates={today_espn}", "ATP"),
        _fetch_espn(f"{_ESPN_WTA_BASE}?dates={today_espn}", "WTA"),
    )
    espn_matches = atp + wta
    if espn_matches:
        logger.info(f"[Source: ESPN] {len(espn_matches)} total matches today")
        return espn_matches

    logger.warning(f"ESPN yielded 0 matches — falling back to TheSportsDB")

    # --- Tier 3: TheSportsDB ---
    tsdb_matches = await _fetch_thesportsdb(today_tsdb)
    logger.info(f"[Source: TheSportsDB] {len(tsdb_matches)} total matches today")
    return tsdb_matches


async def fetch_upcoming_matches() -> list[dict]:
    """Fetch today's scheduled/live matches (for pre-loading players)."""
    all_today = await fetch_all_today()
    return [m for m in all_today if m["status"] in ("scheduled", "live")]
