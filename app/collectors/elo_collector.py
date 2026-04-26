"""
Daily ELO download from Tennis Abstract (Jeff Sackmann).
Scrapes https://tennisabstract.com/reports/atp_elo_ratings.html
and the WTA equivalent once per day.
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

logger = logging.getLogger(__name__)

ATP_ELO_URL = "https://tennisabstract.com/reports/atp_elo_ratings.html"
WTA_ELO_URL = "https://tennisabstract.com/reports/wta_elo_ratings.html"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TennisBotEloCollector/1.0)"
}


def _clean_name(name: str) -> str:
    return name.strip().replace("\xa0", " ")


async def _fetch_elo_page(url: str) -> list[dict]:
    """Fetch and parse an ELO ratings page from Tennis Abstract."""
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(url, headers=HEADERS)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    table = soup.find("table", id="reportable")
    if not table:
        # Try any table with ELO-looking content
        table = soup.find("table")
    if not table:
        logger.warning(f"No table found at {url}")
        return []

    rows = []
    headers_row = table.find("tr")
    if not headers_row:
        return []

    headers = [th.get_text(strip=True).lower() for th in headers_row.find_all(["th", "td"])]

    def col_idx(candidates: list[str]) -> Optional[int]:
        for c in candidates:
            for i, h in enumerate(headers):
                if c in h:
                    return i
        return None

    name_idx = col_idx(["player", "name"])
    elo_idx = col_idx(["elo", "overall"])
    hard_idx = col_idx(["hard"])
    clay_idx = col_idx(["clay"])
    grass_idx = col_idx(["grass"])
    rank_idx = col_idx(["rank", "#"])

    if name_idx is None or elo_idx is None:
        logger.warning(f"Could not identify columns at {url}. Headers: {headers}")
        return []

    for tr in table.find_all("tr")[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if len(cells) <= max(filter(lambda x: x is not None, [name_idx, elo_idx])):
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

        name = _clean_name(cells[name_idx])
        elo = safe_float(elo_idx)
        if not name or not elo:
            continue

        rows.append({
            "name": name,
            "elo": elo,
            "elo_hard": safe_float(hard_idx),
            "elo_clay": safe_float(clay_idx),
            "elo_grass": safe_float(grass_idx),
            "ranking": safe_int(rank_idx),
        })

    return rows


async def refresh_elo(tour: str, db: AsyncSession) -> int:
    """
    Fetch current ELO ratings and upsert into players table.
    Returns number of players updated.
    """
    url = ATP_ELO_URL if tour == "ATP" else WTA_ELO_URL
    logger.info(f"Fetching {tour} ELO from {url}")

    try:
        rows = await _fetch_elo_page(url)
    except Exception as e:
        logger.error(f"Failed to fetch ELO for {tour}: {e}")
        return 0

    today = date.today()
    updated = 0

    for row in rows:
        result = await db.execute(select(Player).where(Player.name == row["name"]))
        player = result.scalar_one_or_none()

        if player is None:
            player = Player(name=row["name"], tour=tour)
            db.add(player)

        player.current_elo = row["elo"]
        if row["elo_hard"]:
            player.elo_hard = row["elo_hard"]
        if row["elo_clay"]:
            player.elo_clay = row["elo_clay"]
        if row["elo_grass"]:
            player.elo_grass = row["elo_grass"]
        if row["ranking"]:
            player.ranking = row["ranking"]
        player.elo_updated_at = today

        # Track peak
        if player.peak_elo is None or (row["elo"] and row["elo"] > player.peak_elo):
            player.peak_elo = row["elo"]

        updated += 1

    await db.commit()
    logger.info(f"Updated ELO for {updated} {tour} players")
    return updated


async def get_player_elo(name: str, surface: str, db: AsyncSession) -> float:
    """Fetch a player's ELO for given surface, with fallback to overall ELO."""
    result = await db.execute(
        select(Player).where(Player.name == name)
    )
    player = result.scalar_one_or_none()
    if player is None:
        return 1500.0  # default ELO
    return player.surface_elo(surface)
