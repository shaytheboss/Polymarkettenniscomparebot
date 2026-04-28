"""
Polymarket live price collector for tennis match markets.

IMPORTANT — Cloud IP blocking:
  Polymarket's API (gamma-api.polymarket.com) blocks requests from cloud
  hosting IPs (Railway, GCP, AWS) via Cloudflare WAF.

  To fix, set POLYMARKET_PROXY_URL in Railway env vars to an HTTP/SOCKS
  proxy with a residential IP:
    POLYMARKET_PROXY_URL=http://user:pass@proxy.example.com:8080
    POLYMARKET_PROXY_URL=socks5://user:pass@proxy.example.com:1080

  Free option: webshare.io (10 free residential proxies)
  Paid option: brightdata.com, oxylabs.io, smartproxy.com

Strategy:
  1. Every 5 minutes, fetch ALL active tennis markets in one batch call
  2. Cache them in memory — no per-match search queries needed
  3. Match against cache by scoring both player names in market question text
  4. Return price for the best-matching market
"""
from __future__ import annotations
import json
import logging
import time
from typing import Optional

import httpx

from app.utils.name_matcher import _last_name, _normalize

logger = logging.getLogger(__name__)

POLY_GAMMA_BASE = "https://gamma-api.polymarket.com"

_BROWSER_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://polymarket.com/",
    "Origin": "https://polymarket.com",
}

# In-memory market cache shared across all calls
_cache: dict = {"markets": [], "fetched_at": 0.0, "last_error": ""}
_CACHE_TTL = 300  # 5 minutes


# ---------------------------------------------------------------------------
# HTTP client factory
# ---------------------------------------------------------------------------

def _client(timeout: float = 12.0) -> httpx.AsyncClient:
    """Create httpx client, routing through proxy if POLYMARKET_PROXY_URL is set."""
    from app.config import settings
    kwargs: dict = {
        "timeout": timeout,
        "headers": _BROWSER_HEADERS,
        "follow_redirects": True,
    }
    proxy = settings.polymarket_proxy_url
    if proxy:
        kwargs["proxies"] = {"https://": proxy, "http://": proxy}
    return httpx.AsyncClient(**kwargs)


# ---------------------------------------------------------------------------
# Market cache — one batch fetch, all matches search locally
# ---------------------------------------------------------------------------

async def _fetch_markets_batch(tag_slug: Optional[str], limit: int = 200) -> list[dict]:
    """Fetch a batch of active markets from the Gamma API."""
    params: dict = {"active": "true", "closed": "false", "_limit": limit}
    if tag_slug:
        params["tag_slug"] = tag_slug

    try:
        async with _client() as c:
            resp = await c.get(f"{POLY_GAMMA_BASE}/markets", params=params)
            resp.raise_for_status()
            data = resp.json()
        return data if isinstance(data, list) else []
    except httpx.HTTPStatusError as e:
        _cache["last_error"] = f"HTTP {e.response.status_code}: {e.response.text[:80]}"
        logger.warning(f"Polymarket API HTTP {e.response.status_code} (tag={tag_slug})")
        return []
    except Exception as e:
        _cache["last_error"] = f"{type(e).__name__}: {e}"
        logger.warning(f"Polymarket API error (tag={tag_slug}): {type(e).__name__}: {e}")
        return []


async def _refresh_cache() -> list[dict]:
    """
    Refresh market cache by fetching all active tennis markets.
    Tries multiple strategies: tennis tag, then no tag.
    """
    global _cache

    markets: list[dict] = []

    # Strategy 1: fetch all tennis-tagged active markets
    markets = await _fetch_markets_batch(tag_slug="tennis", limit=200)
    if markets:
        logger.info(f"Polymarket cache refreshed: {len(markets)} markets (tag=tennis)")

    # Strategy 2: no tag filter — broader search
    if not markets:
        markets = await _fetch_markets_batch(tag_slug=None, limit=100)
        if markets:
            logger.info(f"Polymarket cache refreshed: {len(markets)} markets (no tag)")

    _cache["markets"] = markets
    _cache["fetched_at"] = time.time()
    return markets


async def _get_cached_markets() -> list[dict]:
    """Return cached markets, auto-refreshing when stale."""
    if time.time() - _cache["fetched_at"] > _CACHE_TTL or not _cache["markets"]:
        return await _refresh_cache()
    return _cache["markets"]


# ---------------------------------------------------------------------------
# Direct condition-ID price lookup (when market already linked)
# ---------------------------------------------------------------------------

async def fetch_market_price(condition_id: str) -> Optional[float]:
    """Fetch P(player1 wins) for a known Polymarket condition ID."""
    try:
        async with _client() as c:
            resp = await c.get(
                f"{POLY_GAMMA_BASE}/markets",
                params={"condition_id": condition_id},
            )
            resp.raise_for_status()
            data = resp.json()
        market = data[0] if isinstance(data, list) and data else (data or {})
        return _extract_price(market, player_idx=0)
    except Exception as e:
        logger.debug(f"fetch_market_price({condition_id[:10]}...): {e}")
        return None


# ---------------------------------------------------------------------------
# Name matching
# ---------------------------------------------------------------------------

def _name_score(question_norm: str, p1: str, p2: str) -> float:
    """Score 0–2: how well both player names appear in the question."""
    score = 0.0
    for name in [p1, p2]:
        last = _last_name(name)
        full = _normalize(name)
        if last in question_norm:
            score += 1.0
        elif full in question_norm:
            score += 0.8
        elif len(last) >= 4 and last[:4] in question_norm:
            score += 0.4
    return score


# ---------------------------------------------------------------------------
# Auto-discovery from cache
# ---------------------------------------------------------------------------

async def search_tennis_markets(player1: str, player2: str) -> list[dict]:
    """
    Find the Polymarket market for this matchup by searching the market cache.
    Returns list of candidates sorted by name-match score (best first).
    """
    all_markets = await _get_cached_markets()
    if not all_markets:
        return []

    scored = []
    for market in all_markets:
        question = _normalize(market.get("question", "") or market.get("title", ""))
        score = _name_score(question, player1, player2)
        if score >= 1.0:  # must match at least one full last name
            scored.append((score, market))

    scored.sort(key=lambda x: x[0], reverse=True)

    if scored:
        best = scored[0][1]
        logger.info(
            f"Polymarket found [{scored[0][0]:.1f}] {player1} vs {player2}: "
            f"'{best.get('question', best.get('title', ''))[:80]}'"
        )
    else:
        logger.debug(
            f"Polymarket: no match for {player1} vs {player2} "
            f"(searched {len(all_markets)} cached markets)"
        )

    return [m for _, m in scored]


# ---------------------------------------------------------------------------
# Price extraction
# ---------------------------------------------------------------------------

def _extract_price(market: dict, player_idx: int = 0) -> Optional[float]:
    """Extract the win probability from a market's outcomePrices array."""
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

    if isinstance(prices_raw, dict):
        keys = list(prices_raw.keys())
        if keys:
            try:
                return float(prices_raw[keys[player_idx]])
            except Exception:
                pass

    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def fetch_match_price(
    player1: str,
    player2: str,
    condition_id: Optional[str] = None,
) -> tuple[Optional[float], Optional[str], Optional[str]]:
    """
    Get P(player1 wins) from Polymarket.

    Returns (price, condition_id, slug):
      price       — float in [0,1], P(player1 wins), or None if not found
      condition_id — Polymarket market condition ID, or None
      slug         — market slug for https://polymarket.com/event/{slug}, or None

    If condition_id is already known, fetches directly.
    Otherwise searches all cached markets by player name.
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

    outcomes_raw = best.get("outcomes") or "[]"
    if isinstance(outcomes_raw, str):
        try:
            outcomes = json.loads(outcomes_raw)
        except Exception:
            outcomes = []
    else:
        outcomes = list(outcomes_raw)

    # Find which outcome index corresponds to player1
    last1 = _last_name(player1)
    player1_idx = 0
    for i, outcome_name in enumerate(outcomes):
        if last1 in _normalize(str(outcome_name)):
            player1_idx = i
            break

    price = _extract_price(best, player_idx=player1_idx)
    return price, cid, slug


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

async def test_connectivity() -> dict:
    """
    Test Polymarket API connectivity.
    Returns diagnostic dict — call from /polytest Telegram command.
    """
    from app.config import settings
    proxy = settings.polymarket_proxy_url

    result: dict = {
        "proxy_configured": bool(proxy),
        "proxy_hint": (proxy[:30] + "...") if proxy else "not set",
        "ok": False,
        "http_status": None,
        "markets_found": 0,
        "sample_question": None,
        "last_cache_error": _cache.get("last_error", ""),
    }

    try:
        async with _client(timeout=15.0) as c:
            resp = await c.get(
                f"{POLY_GAMMA_BASE}/markets",
                params={"active": "true", "closed": "false", "_limit": 5, "tag_slug": "tennis"},
            )
            result["http_status"] = resp.status_code
            if resp.status_code == 200:
                data = resp.json()
                markets = data if isinstance(data, list) else []
                result["ok"] = True
                result["markets_found"] = len(markets)
                if markets:
                    result["sample_question"] = markets[0].get("question", "N/A")[:80]
    except Exception as e:
        result["http_status"] = f"{type(e).__name__}: {e}"

    return result
