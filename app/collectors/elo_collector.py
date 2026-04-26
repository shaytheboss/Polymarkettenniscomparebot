"""
Daily ELO download from Tennis Abstract (Jeff Sackmann).
Scrapes https://tennisabstract.com/reports/atp_elo_ratings.html (and WTA equivalent).

Player records store both the canonical Tennis Abstract name AND a normalized
last-name index so we can fuzzy-match names from ESPN and Polymarket.
"""
from __future__ import annotations
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

ATP_ELO_URL = "https://tennisabstract.com/reports/atp_elo_ratings.html"
WTA_ELO_URL = "https://tennisabstract.com/reports/wta_elo_ratings.html"

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TennisArbBot/1.0)"}


def _clean_name(name: str) -> str:
    return name.strip().replace("\xa0", " ")


async def _fetch_elo_page(url: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(url, headers=_HEADERS)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    table = soup.find("table", id="reportable") or soup.find("table")
    if not table:
        logger.warning(f"No ELO table at {url}")
        return []

    rows_el = table.find_all("tr")
    if not rows_el:
        return []

    headers = [th.get_text(strip=True).lower() for th in rows_el[0].find_all(["th", "td"])]

    def col_idx(candidates: list[str]) -> Optional[int]:
        for cand in candidates:
            for i, h in enumerate(headers):
                if cand in h:
                    return i
        return None

    name_idx  = col_idx(["player", "name"])
    elo_idx   = col_idx(["elo", "overall"])
    hard_idx  = col_idx(["hard"])
    clay_idx  = col_idx(["clay"])
    grass_idx = col_idx(["grass"])
    rank_idx  = col_idx(["rank", "#"])

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
        if not name or not elo:
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


async def refresh_elo(tour: str, db: AsyncSession) -> int:
    """Fetch and upsert ELO ratings. Returns count updated."""
    url = ATP_ELO_URL if tour == "ATP" else WTA_ELO_URL
    logger.info(f"Fetching {tour} ELO from {url}")

    try:
        rows = await _fetch_elo_page(url)
    except Exception as e:
        logger.error(f"ELO fetch failed for {tour}: {e}")
        return 0

    today = date.today()
    updated = 0

    for row in rows:
        result = await db.execute(select(Player).where(Player.name == row["name"]))
        player = result.scalar_one_or_none()

        if player is None:
            player = Player(name=row["name"], tour=tour)
            player.name_last = _last_name(row["name"])
            db.add(player)

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
# Name-aware ELO lookup (used by jobs.py)
# ---------------------------------------------------------------------------

async def find_player_by_name(
    name: str,
    tour: str,
    db: AsyncSession,
    fuzzy_threshold: float = 0.80,
) -> Optional[Player]:
    """
    Find a player in DB matching name, using a two-step approach:
    1. DB query by normalized last name (fast, indexed)
    2. Fuzzy match on the resulting candidates (precise)
    """
    from app.utils.name_matcher import match_name

    # Step 0: Exact match
    result = await db.execute(
        select(Player).where(Player.name == name, Player.tour == tour)
    )
    player = result.scalar_one_or_none()
    if player:
        return player

    # Step 1: Last-name DB filter (narrows to ~1-5 candidates)
    query_last = _last_name(name)
    result = await db.execute(
        select(Player).where(
            Player.tour == tour,
            Player.name_last == query_last,
        )
    )
    last_candidates = result.scalars().all()
    if last_candidates:
        if len(last_candidates) == 1:
            return last_candidates[0]
        # Multiple players with same last name — fuzzy on full name
        names = [p.name for p in last_candidates]
        matched = match_name(name, names, threshold=fuzzy_threshold)
        if matched:
            for p in last_candidates:
                if p.name == matched:
                    return p

    # Step 2: Full fuzzy scan (fallback — slower but catches typos/accent diffs)
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
