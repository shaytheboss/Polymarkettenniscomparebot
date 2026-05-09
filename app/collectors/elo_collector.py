"""
Daily ELO download for the tennis bot.

Primary source: Tennis Abstract (Jeff Sackmann) HTML table.
  ATP: https://tennisabstract.com/reports/atp_elo_ratings.html
  WTA: https://tennisabstract.com/reports/wta_elo_ratings.html

  Tennis Abstract blocks Railway/GCP/AWS cloud IPs (HTTP 403).
  Fix: deploy cloudflare-worker.js and set POLYMARKET_RELAY_URL.
  The worker routes /tennis-abstract/* → tennisabstract.com.

Fallback: Jeff Sackmann's GitHub match CSVs (always accessible from Railway).
  https://github.com/JeffSackmann/tennis_atp  (atp_matches_YEAR.csv)
  https://github.com/JeffSackmann/tennis_wta  (wta_matches_YEAR.csv)

  ELO is computed from last 3 available years using standard K=32 formula.
  Surface ELO (hard/clay/grass) is updated per match surface.
  Compared to Tennis Abstract: relative rankings are accurate, absolute
  values differ slightly because we start from a 3-year window (not 50+).
"""
from __future__ import annotations
import csv
import io
import logging
import re
from datetime import date
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.player import Player
from app.utils.name_matcher import _normalize, _last_name

logger = logging.getLogger(__name__)

ATP_ELO_PATH = "/reports/atp_elo_ratings.html"
WTA_ELO_PATH = "/reports/wta_elo_ratings.html"

GITHUB_BASE_ATP = "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master"
GITHUB_BASE_WTA = "https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}


def _ta_url(path: str) -> str:
    """Build Tennis Abstract URL, routing through Polymarket relay if configured."""
    from app.config import settings
    relay = settings.polymarket_relay_url.rstrip("/")
    if relay:
        # Same Cloudflare Worker that proxies Polymarket — /tennis-abstract/* routes to TA
        return f"{relay}/tennis-abstract{path}"
    return f"https://tennisabstract.com{path}"


def _clean_name(name: str) -> str:
    return name.strip().replace("\xa0", " ")


# ---------------------------------------------------------------------------
# Primary: Tennis Abstract HTML scraper
# ---------------------------------------------------------------------------

async def _fetch_elo_page(path: str) -> list[dict]:
    """Scrape ELO table from Tennis Abstract. Returns [] if blocked or unavailable."""
    url = _ta_url(path)
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url, headers=_HEADERS)
            if resp.status_code == 403:
                logger.warning(
                    f"Tennis Abstract blocked (HTTP 403) at {url} — "
                    f"deploy cloudflare-worker.js and set POLYMARKET_RELAY_URL, "
                    f"or use GitHub CSV fallback (automatic)"
                )
                return []
            resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        logger.warning(f"Tennis Abstract HTTP {e.response.status_code} at {url}")
        return []
    except Exception as e:
        logger.warning(f"Tennis Abstract unreachable: {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    table = soup.find("table", id="reportable") or soup.find("table")
    if not table:
        logger.warning(f"No ELO table at {url}")
        return []

    rows_el = table.find_all("tr")
    if not rows_el:
        return []

    headers = [
        th.get_text(strip=True).replace("\xa0", " ").lower()
        for th in rows_el[0].find_all(["th", "td"])
    ]
    logger.info(f"ELO table headers: {headers}")

    def col_exact(candidates: list[str]) -> Optional[int]:
        for i, h in enumerate(headers):
            if h in candidates:
                return i
        return None

    def col_contains(candidates: list[str], excludes: list[str] = None) -> Optional[int]:
        excl = excludes or []
        for cand in candidates:
            for i, h in enumerate(headers):
                if cand in h and not any(ex in h for ex in excl):
                    return i
        return None

    def _col(exact_candidates: list[str], contains_candidates: list[str] = None,
             excludes: list[str] = None) -> Optional[int]:
        """Return column index: exact match first, then substring match. Handles idx=0."""
        idx = col_exact(exact_candidates)
        if idx is not None:
            return idx
        return col_contains(contains_candidates or exact_candidates, excludes)

    _surface_terms = ["hard", "clay", "grass", "carpet", "h.", "c.", "g.", "h-", "c-", "g-"]
    name_idx  = _col(["player", "name"])
    elo_idx   = _col(["elo", "overall elo", "overall"],
                     ["elo", "overall"], excludes=_surface_terms)
    hard_idx  = _col(["h-elo", "helo", "h.elo", "hard elo"], ["h-elo", "h.elo", "helo"])
    clay_idx  = _col(["c-elo", "celo", "c.elo", "clay elo"], ["c-elo", "c.elo", "celo"])
    grass_idx = _col(["g-elo", "gelo", "g.elo", "grass elo"], ["g-elo", "g.elo", "gelo"])
    rank_idx  = _col(["atp rank", "wta rank", "rank", "#", "rk", "ranking"],
                     ["rank", "rk"], excludes=["elo"])

    logger.info(
        f"ELO columns — name:{name_idx} elo:{elo_idx} "
        f"hard:{hard_idx} clay:{clay_idx} grass:{grass_idx} rank:{rank_idx}"
    )
    if name_idx is None or elo_idx is None:
        logger.warning(f"Cannot identify ELO columns. Headers: {headers}")
        return []

    rows = []
    for tr in rows_el[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if len(cells) < 2:
            continue

        def safe_float(idx: Optional[int]) -> Optional[float]:
            if idx is None or idx >= len(cells):
                return None
            txt = re.sub(r"[^\d.]", "", cells[idx])
            try:
                return float(txt) if txt else None
            except ValueError:
                return None

        def safe_int(idx: Optional[int]) -> Optional[int]:
            if idx is None or idx >= len(cells):
                return None
            txt = re.sub(r"[^\d]", "", cells[idx])
            try:
                return int(txt) if txt else None
            except ValueError:
                return None

        name = _clean_name(cells[name_idx]) if name_idx < len(cells) else ""
        elo  = safe_float(elo_idx)
        if not name or not elo or not (1200 <= elo <= 2800):
            continue

        rows.append({
            "name":      name,
            "elo":       elo,
            "elo_hard":  safe_float(hard_idx),
            "elo_clay":  safe_float(clay_idx),
            "elo_grass": safe_float(grass_idx),
            "ranking":   safe_int(rank_idx),
        })

    return rows


# ---------------------------------------------------------------------------
# Fallback: compute ELO from Jeff Sackmann's GitHub match CSVs
# ---------------------------------------------------------------------------

async def _compute_elo_from_github(tour: str) -> list[dict]:
    """
    Compute ELO from Jeff Sackmann's GitHub match data when Tennis Abstract is blocked.
    Downloads the last 3 available years and computes surface ELO using K=32.

    Three CSV families per tour are merged so ELO covers newer/lower-ranked players
    who only appear at qualifying / Challenger / ITF level:
      ATP: atp_matches_YYYY, atp_matches_qual_chall_YYYY, atp_matches_futures_YYYY
      WTA: wta_matches_YYYY, wta_matches_qual_itf_YYYY
    """
    tour_lower = tour.lower()
    base = GITHUB_BASE_ATP if tour == "ATP" else GITHUB_BASE_WTA
    current_year = date.today().year

    if tour == "ATP":
        suffixes = ["", "_qual_chall", "_futures"]
    else:
        suffixes = ["", "_qual_itf"]

    all_rows: list[dict] = []
    years_loaded: set[str] = set()

    async with httpx.AsyncClient(timeout=40, headers=_HEADERS) as c:
        for year in range(current_year, current_year - 4, -1):
            for suffix in suffixes:
                url = f"{base}/{tour_lower}_matches{suffix}_{year}.csv"
                try:
                    resp = await c.get(url)
                    if resp.status_code == 200:
                        reader = csv.DictReader(io.StringIO(resp.text))
                        rows = list(reader)
                        all_rows.extend(rows)
                        years_loaded.add(str(year))
                        logger.info(
                            f"GitHub ELO: loaded {len(rows)} {tour} matches "
                            f"from {year}{suffix or ' main'}"
                        )
                    elif resp.status_code == 404:
                        logger.debug(f"GitHub ELO: {tour} {year}{suffix} not published")
                    else:
                        logger.warning(f"GitHub ELO: {url} → HTTP {resp.status_code}")
                except Exception as e:
                    logger.warning(f"GitHub ELO: failed to fetch {tour} {year}{suffix}: {e}")

    if not all_rows:
        logger.error(f"GitHub ELO fallback: no {tour} match data available")
        return []

    logger.info(
        f"GitHub ELO: computing from {len(all_rows)} {tour} matches "
        f"({', '.join(sorted(years_loaded))})"
    )

    # Sort chronologically — tourney_date is YYYYMMDD string, sorts lexicographically
    try:
        all_rows.sort(key=lambda m: (m.get("tourney_date", "0"), m.get("match_num", "0")))
    except Exception:
        pass

    player_elo: dict[str, float] = {}
    surface_elo: dict[str, dict[str, float]] = {}
    player_rank: dict[str, int] = {}
    K = 32.0

    def _elo(name: str) -> float:
        return player_elo.get(name, 1500.0)

    def _surf_elo(name: str, surf: str) -> float:
        return surface_elo.get(name, {}).get(surf, 1500.0)

    def _update_pair(winner: str, loser: str, surf: str) -> None:
        ew, el = _elo(winner), _elo(loser)
        exp_w = 1.0 / (1.0 + 10.0 ** ((el - ew) / 400.0))
        player_elo[winner] = ew + K * (1.0 - exp_w)
        player_elo[loser]  = el + K * (0.0 - (1.0 - exp_w))

        if surf in ("hard", "clay", "grass"):
            sw = _surf_elo(winner, surf)
            sl = _surf_elo(loser, surf)
            exp_sw = 1.0 / (1.0 + 10.0 ** ((sl - sw) / 400.0))
            surface_elo.setdefault(winner, {})[surf] = sw + K * (1.0 - exp_sw)
            surface_elo.setdefault(loser, {})[surf]  = sl + K * (0.0 - (1.0 - exp_sw))

    for row in all_rows:
        w_name = (row.get("winner_name") or "").strip()
        l_name = (row.get("loser_name") or "").strip()
        if not w_name or not l_name or w_name == l_name:
            continue

        surf_raw = (row.get("surface") or "Hard").lower()
        surf = "clay" if surf_raw == "clay" else ("grass" if surf_raw == "grass" else "hard")

        _update_pair(w_name, l_name, surf)

        # Track most recent ranking for each player
        try:
            wr = int(row.get("winner_rank") or 0)
            lr = int(row.get("loser_rank") or 0)
            if wr > 0:
                player_rank[w_name] = wr
            if lr > 0:
                player_rank[l_name] = lr
        except (ValueError, TypeError):
            pass

    # Build output, sorted by overall ELO descending
    result = []
    for name, elo in sorted(player_elo.items(), key=lambda x: -x[1]):
        if not (1200.0 <= elo <= 2800.0):
            continue
        surf = surface_elo.get(name, {})
        result.append({
            "name":      name,
            "elo":       round(elo, 1),
            "elo_hard":  round(surf.get("hard", elo), 1),
            "elo_clay":  round(surf.get("clay", elo), 1),
            "elo_grass": round(surf.get("grass", elo), 1),
            "ranking":   player_rank.get(name),
        })

    latest_year = max(years_loaded) if years_loaded else "?"
    logger.info(
        f"GitHub ELO computed: {len(result)} {tour} players "
        f"(data through end of {latest_year})"
    )
    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def refresh_elo(tour: str, db: AsyncSession) -> int:
    """
    Fetch and upsert ELO ratings. Returns count updated.

    Try order:
      1. Tennis Abstract HTML (direct, or via Cloudflare Worker relay if POLYMARKET_RELAY_URL set)
      2. Jeff Sackmann's GitHub CSVs (always accessible — computed from last 3 years of matches)
    """
    path = ATP_ELO_PATH if tour == "ATP" else WTA_ELO_PATH
    logger.info(f"Fetching {tour} ELO from {_ta_url(path)}")

    rows = await _fetch_elo_page(path)

    if not rows:
        logger.info(f"Tennis Abstract unavailable — using GitHub CSV fallback for {tour} ELO")
        rows = await _compute_elo_from_github(tour)

    if not rows:
        logger.error(f"All ELO sources failed for {tour}")
        return 0

    today = date.today()
    updated = 0

    for row in rows:
        result = await db.execute(select(Player).where(Player.name == row["name"]))
        player = result.scalar_one_or_none()

        if player is None:
            player = await find_player_by_name(row["name"], tour, db, fuzzy_threshold=0.85)

        if player is None:
            player = Player(name=row["name"], tour=tour)
            player.name_last = _last_name(row["name"])
            db.add(player)
        else:
            if not player.name_last:
                player.name_last = _last_name(player.name)

        player.current_elo = row["elo"]
        if row["elo_hard"]:  player.elo_hard  = row["elo_hard"]
        if row["elo_clay"]:  player.elo_clay  = row["elo_clay"]
        if row["elo_grass"]: player.elo_grass = row["elo_grass"]
        if row["ranking"]:   player.ranking   = row["ranking"]
        player.elo_updated_at = today
        if player.peak_elo is None or row["elo"] > player.peak_elo:
            player.peak_elo = row["elo"]

        updated += 1

    await db.commit()
    logger.info(f"ELO updated: {updated} {tour} players")
    return updated


# ---------------------------------------------------------------------------
# Name-aware ELO lookup
# ---------------------------------------------------------------------------

async def find_player_by_name(
    name: str,
    tour: str,
    db: AsyncSession,
    fuzzy_threshold: float = 0.80,
) -> Optional[Player]:
    """
    Find a player in DB matching name.
    Step 0: exact name + tour
    Step 1: last-name DB filter, then fuzzy if multiple candidates
    Step 2: full fuzzy scan (fallback — slower)
    """
    from app.utils.name_matcher import match_name

    result = await db.execute(
        select(Player).where(Player.name == name, Player.tour == tour)
    )
    player = result.scalar_one_or_none()
    if player:
        return player

    query_last = _last_name(name)
    result = await db.execute(
        select(Player).where(Player.tour == tour, Player.name_last == query_last)
    )
    last_candidates = result.scalars().all()
    if last_candidates:
        if len(last_candidates) == 1:
            return last_candidates[0]
        names = [p.name for p in last_candidates]
        matched = match_name(name, names, threshold=fuzzy_threshold)
        if matched:
            for p in last_candidates:
                if p.name == matched:
                    return p

    result = await db.execute(select(Player).where(Player.tour == tour))
    all_players = result.scalars().all()
    if not all_players:
        return None

    all_names = [p.name for p in all_players]
    matched_name = match_name(name, all_names, threshold=fuzzy_threshold)
    if matched_name:
        for p in all_players:
            if p.name == matched_name:
                return p

    return None


async def get_player_elo(
    name: str,
    surface: str,
    tour: str,
    db: AsyncSession,
) -> float:
    """Return surface ELO for player, or 1500.0 default if unknown."""
    player = await find_player_by_name(name, tour, db)
    if player is None:
        return 1500.0
    return player.surface_elo(surface)
