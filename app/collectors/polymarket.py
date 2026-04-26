"""
Polymarket live price collector for tennis match markets.
Uses Polymarket CLOB API to fetch orderbook prices.
"""
from __future__ import annotations
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

POLY_CLOB_BASE = "https://clob.polymarket.com"
POLY_GAMMA_BASE = "https://gamma-api.polymarket.com"


async def fetch_market_price(condition_id: str) -> Optional[float]:
    """
    Fetch P(YES) price for a Polymarket condition ID.
    condition_id is the on-chain condition hash for the market.
    Returns float in [0, 1] or None if unavailable.
    """
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                f"{POLY_GAMMA_BASE}/markets",
                params={"condition_id": condition_id},
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()

        if not data:
            return None

        market = data[0] if isinstance(data, list) else data
        outcomes = market.get("outcomes", [])
        prices = market.get("outcomePrices", [])

        if not prices:
            return None

        # First outcome = YES (player 1 wins)
        if isinstance(prices, list) and prices:
            try:
                return float(prices[0])
            except (ValueError, TypeError):
                pass

        if isinstance(prices, str):
            import json
            try:
                pl = json.loads(prices)
                return float(pl[0]) if pl else None
            except Exception:
                pass

        return None
    except Exception as e:
        logger.debug(f"Polymarket price fetch failed for {condition_id}: {e}")
        return None


async def search_tennis_markets(player1: str, player2: str) -> list[dict]:
    """
    Search Polymarket for markets matching a tennis match.
    Returns list of matching market dicts.
    """
    query = f"{player1} {player2} tennis"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{POLY_GAMMA_BASE}/markets",
                params={
                    "tag_slug": "tennis",
                    "active": "true",
                    "closed": "false",
                    "_limit": 20,
                    "q": query,
                },
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.debug(f"Polymarket search failed: {e}")
        return []


async def fetch_clob_price(token_id: str) -> Optional[float]:
    """
    Fetch mid-price from CLOB orderbook for a specific token.
    token_id = the YES token ID for a market outcome.
    """
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                f"{POLY_CLOB_BASE}/midpoint",
                params={"token_id": token_id},
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
        return float(data.get("mid", 0)) or None
    except Exception as e:
        logger.debug(f"CLOB price fetch failed: {e}")
        return None
