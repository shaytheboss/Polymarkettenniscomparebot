"""
Polymarket live price collector for tennis match markets.

Player name matching challenge:
  Our DB name:        "Novak Djokovic"
  Polymarket market:  "Will Djokovic win the match against Alcaraz?"
                   or "Djokovic vs Alcaraz"
                   or "N. Djokovic to win"

Strategy:
  1. Search Gamma API with player last names
  2. Score each result by fuzzy name presence in question text
  3. Return the best matching market's price for P(player1 wins)
"""
from __future__ import annotations
import logging
import re
from typing import Optional

import httpx

from app.utils.name_matcher import _last_name, _normalize, normalize_for_polymarket

logger = logging.getLogger(__name__)

POLY_GAMMA_BASE = "https://gamma-api.polymarket.com"
POLY_CLOB_BASE  = "https://clob.polymarket.com"

_HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}


# ---------------------------------------------------------------------------
# Direct condition-ID lookup
# ---------------------------------------------------------------------------

async def fetch_market_price(condition_id: str) -> Optional[float]:
    """
    Fetch P(YES/player1) for a known Polymarket condition ID.
    Returns float in [0,1] or None.
    """
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                f"{POLY_GAMMA_BASE}/markets",
                params={"condition_id": condition_id},
                headers=_HEADERS,
            )
            resp.raise_for_status()
            data = resp.json()

        market = data[0] if isinstance(data, list) and data else (data or {})
        return _extract_price(market, player_idx=0)
    except Exception as e:
        logger.debug(f"fetch_market_price({condition_id}): {e}")
        return None


# ---------------------------------------------------------------------------
# Auto-discovery by player names
# ---------------------------------------------------------------------------

async def search_tennis_markets(player1: str, player2: str) -> list[dict]:
    """
    Search Polymarket Gamma API for a market matching this match.
    Returns list of candidate market dicts ordered by relevance score.
    Tries multiple search strategies with graceful fallback.
    """
    last1 = _last_name(player1)
    last2 = _last_name(player2)

    candidates: list[dict] = []
    seen_ids: set[str] = set()

    def _add(results: list[dict]) -> None:
        for r in results:
            uid = r.get("conditionId") or r.get("condition_id") or r.get("id") or r.get("slug") or ""
            if uid and uid not in seen_ids:
                seen_ids.add(uid)
                candidates.append(r)

    # Strategy 1: combined query with tennis tag
    _add(await _gamma_search(f"{last1} {last2}", tag_slug="tennis"))
    # Strategy 2: individual names with tennis tag
    if not candidates:
        _add(await _gamma_search(last1, tag_slug="tennis"))
        _add(await _gamma_search(last2, tag_slug="tennis"))
    # Strategy 3: combined without tag (broader search)
    if not candidates:
        _add(await _gamma_search(f"{last1} {last2}", tag_slug=None))
    # Strategy 4: individual names without tag
    if not candidates:
        _add(await _gamma_search(last1, tag_slug=None))
        _add(await _gamma_search(last2, tag_slug=None))

    if not candidates:
        logger.info(f"Polymarket: no markets found for {player1} vs {player2}")
        return []

    # Score by how well both player names appear in the question
    scored = []
    for market in candidates:
        question = _normalize(market.get("question", "") or market.get("title", ""))
        score = _name_presence_score(question, player1, player2)
        if score > 0:
            scored.append((score, market))

    scored.sort(key=lambda x: x[0], reverse=True)

    if scored:
        best = scored[0][1]
        logger.info(
            f"Polymarket match [{scored[0][0]:.1f}] for {player1} vs {player2}: "
            f"'{best.get('question', best.get('title', 'N/A'))[:80]}'"
        )
    else:
        logger.info(
            f"Polymarket: {len(candidates)} markets found but none name-match "
            f"{player1} vs {player2}"
        )

    return [m for _, m in scored]


def _name_presence_score(question_norm: str, p1: str, p2: str) -> float:
    """
    Score 0-2: how many of the two players' names appear in the question text.
    Checks last name, full name, and initial+last.
    """
    score = 0.0
    for name in [p1, p2]:
        last = _last_name(name)
        full = _normalize(name)
        if last in question_norm:
            score += 1.0
        elif full in question_norm:
            score += 0.8
        else:
            # Partial: at least first 4 chars of last name
            if len(last) >= 4 and last[:4] in question_norm:
                score += 0.4
    return score


async def _gamma_search(query: str, tag_slug: Optional[str] = "tennis") -> list[dict]:
    """Search Gamma API for markets matching query."""
    params: dict = {
        "active": "true",
        "closed": "false",
        "_limit": 50,
        "q": query,
    }
    if tag_slug:
        params["tag_slug"] = tag_slug

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{POLY_GAMMA_BASE}/markets",
                params=params,
                headers=_HEADERS,
            )
            resp.raise_for_status()
            data = resp.json()
        results = data if isinstance(data, list) else []
        if results:
            logger.debug(
                f"Gamma search '{query}' tag={tag_slug}: {len(results)} results"
            )
        return results
    except Exception as e:
        logger.info(f"Gamma search '{query}' tag={tag_slug}: {e}")
        return []


# ---------------------------------------------------------------------------
# Price extraction
# ---------------------------------------------------------------------------

def _extract_price(market: dict, player_idx: int = 0) -> Optional[float]:
    """
    Extract the YES/player probability from a market dict.
    player_idx: 0 = first outcome (typically player1/home), 1 = second.
    """
    import json

    prices_raw = market.get("outcomePrices") or market.get("prices")
    if prices_raw is None:
        return None

    if isinstance(prices_raw, str):
        try:
            prices_raw = json.loads(prices_raw)
        except Exception:
            return None

    if isinstance(prices_raw, list) and len(prices_raw) > player_idx:
        try:
            return float(prices_raw[player_idx])
        except (TypeError, ValueError):
            return None

    # Dict format: {"Yes": 0.7, "No": 0.3}
    if isinstance(prices_raw, dict):
        keys = list(prices_raw.keys())
        if keys:
            try:
                return float(prices_raw[keys[player_idx]])
            except Exception:
                pass

    return None


async def fetch_match_price(
    player1: str,
    player2: str,
    condition_id: Optional[str] = None,
) -> tuple[Optional[float], Optional[str], Optional[str]]:
    """
    Get P(player1 wins) from Polymarket.

    If condition_id is known, fetch directly.
    Otherwise auto-discover by player names.

    Returns (price, condition_id, slug):
      - price: P(player1 wins) in [0,1], or None if not found
      - condition_id: Polymarket condition ID, or None
      - slug: market slug for constructing direct URL, or None

    IMPORTANT: The returned price is always relative to player1 as given.
    The caller must handle name ordering (higher-ELO player first).
    """
    if condition_id:
        price = await fetch_market_price(condition_id)
        return price, condition_id, None

    markets = await search_tennis_markets(player1, player2)
    if not markets:
        return None, None, None

    best = markets[0]
    cid = best.get("conditionId") or best.get("condition_id")
    slug = best.get("slug") or best.get("groupSlug")

    # Determine which outcome corresponds to player1
    # Polymarket outcomes are typically [player1_name, player2_name]
    outcomes_raw = best.get("outcomes") or "[]"
    if isinstance(outcomes_raw, str):
        import json
        try:
            outcomes = json.loads(outcomes_raw)
        except Exception:
            outcomes = []
    else:
        outcomes = outcomes_raw

    # Find which outcome index is player1
    last1 = _last_name(player1)
    player1_idx = 0  # default: first outcome = player1
    for i, outcome_name in enumerate(outcomes):
        if last1 in _normalize(str(outcome_name)):
            player1_idx = i
            break

    price = _extract_price(best, player_idx=player1_idx)
    return price, cid, slug


async def fetch_clob_price(token_id: str) -> Optional[float]:
    """Fetch mid-price from CLOB orderbook for a token."""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                f"{POLY_CLOB_BASE}/midpoint",
                params={"token_id": token_id},
                headers=_HEADERS,
            )
            resp.raise_for_status()
            data = resp.json()
        return float(data.get("mid", 0)) or None
    except Exception as e:
        logger.debug(f"CLOB price: {e}")
        return None
