"""
Polymarket live price collector for tennis match markets.

Architecture:
  1. Market DISCOVERY  — Gamma API /events?tag_id=864  (NOT /markets?tag_slug=tennis)
  2. Price UPDATES     — CLOB API:  batch-fetch prices for all known token IDs in one call
                         Fallback:  Gamma API outcomePrices if CLOB is unavailable

Key fix: the correct endpoint is /events?tag_id=864.
  - /markets?tag_slug=tennis returns no results (wrong API path).
  - /events returns event objects, each with a nested "markets" array.
  - Market URL uses the EVENT slug: https://polymarket.com/event/{event_slug}

CLOB API is faster and more efficient: one request fetches prices for all live matches.

IP blocking:
  Polymarket blocks cloud hosting IPs (Railway, GCP, AWS) via Cloudflare WAF.
  Fix: deploy cloudflare-worker.js and set POLYMARKET_RELAY_URL in Railway.
  The worker routes:
    /gamma/* → gamma-api.polymarket.com
    /clob/*  → clob.polymarket.com
"""
from __future__ import annotations
import json
import logging
import time
from typing import Optional

import httpx

from app.utils.name_matcher import _last_name, _normalize

logger = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE  = "https://clob.polymarket.com"

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

# In-memory market cache for discovery (refreshed every 5 min)
_cache: dict = {"markets": [], "fetched_at": 0.0, "last_error": ""}
_CACHE_TTL = 300


# ---------------------------------------------------------------------------
# URL routing — direct or through Cloudflare Worker relay
# ---------------------------------------------------------------------------

def _gamma_url(path: str) -> str:
    """Build Gamma API URL, routing through relay if configured."""
    from app.config import settings
    relay = settings.polymarket_relay_url.rstrip("/")
    if relay:
        return f"{relay}/gamma{path}"
    return f"{GAMMA_BASE}{path}"


def _clob_url(path: str) -> str:
    """Build CLOB API URL, routing through relay if configured."""
    from app.config import settings
    relay = settings.polymarket_relay_url.rstrip("/")
    if relay:
        return f"{relay}/clob{path}"
    return f"{CLOB_BASE}{path}"


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

def _client(timeout: float = 12.0) -> httpx.AsyncClient:
    """Create httpx client, with optional standard proxy (not needed when using relay)."""
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
# CLOB price fetching (batch — one call for all token IDs)
# ---------------------------------------------------------------------------

async def fetch_clob_prices(token_ids: list[str]) -> dict[str, float]:
    """
    Fetch current mid prices for multiple token IDs in one CLOB API call.
    Returns dict: {token_id: mid_price_float}.
    """
    if not token_ids:
        return {}
    unique_ids = list(dict.fromkeys(token_ids))  # deduplicate, preserve order
    try:
        async with _client() as c:
            resp = await c.get(
                _clob_url("/prices"),
                params={"token_id": ",".join(unique_ids)},
            )
            resp.raise_for_status()
            data = resp.json()
        # Response: {"token_id_1": "0.65", "token_id_2": "0.35"}
        return {k: float(v) for k, v in data.items() if v is not None}
    except httpx.HTTPStatusError as e:
        _cache["last_error"] = f"CLOB HTTP {e.response.status_code}: {e.response.text[:60]}"
        logger.warning(f"CLOB /prices HTTP {e.response.status_code}")
        return {}
    except Exception as e:
        _cache["last_error"] = f"CLOB {type(e).__name__}: {e}"
        logger.warning(f"CLOB /prices error: {type(e).__name__}: {e}")
        return {}


async def fetch_last_trade_price(token_id: str) -> Optional[float]:
    """
    Fetch the price of the last executed trade for a token from the CLOB order book.
    This is more accurate than the mid price — it reflects actual market activity.

    Uses GET /last-trade-price?token_id=TOKEN_ID
    Response: {"price": "0.65"} or {"price": null}
    Falls back to order book mid if last-trade-price is unavailable.
    """
    try:
        async with _client() as c:
            resp = await c.get(
                _clob_url("/last-trade-price"),
                params={"token_id": token_id},
            )
            resp.raise_for_status()
            data = resp.json()
        price = data.get("price")
        if price is not None:
            return float(price)
    except Exception:
        pass

    # Fallback: derive from order book best bid/ask midpoint
    try:
        async with _client() as c:
            resp = await c.get(
                _clob_url("/book"),
                params={"token_id": token_id},
            )
            resp.raise_for_status()
            book = resp.json()
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        if bids and asks:
            best_bid = float(bids[0]["price"])
            best_ask = float(asks[0]["price"])
            return round((best_bid + best_ask) / 2, 4)
    except Exception as e:
        logger.debug(f"CLOB order book fallback failed for {token_id[:12]}: {e}")

    return None


async def fetch_last_trade_prices(token_ids: list[str]) -> dict[str, float]:
    """Fetch last trade prices for multiple token IDs concurrently."""
    import asyncio
    results = await asyncio.gather(
        *[fetch_last_trade_price(t) for t in token_ids],
        return_exceptions=True,
    )
    return {
        tid: price
        for tid, price in zip(token_ids, results)
        if isinstance(price, float)
    }


# ---------------------------------------------------------------------------
# Gamma API market discovery
# ---------------------------------------------------------------------------

async def _fetch_tennis_events(limit: int = 100) -> list[dict]:
    """
    Fetch active tennis markets via /events?tag_id=864.

    The /events endpoint returns event objects with a nested "markets" array.
    We flatten them into individual market dicts and attach _event_slug so we
    can build the correct URL: https://polymarket.com/event/{_event_slug}.
    """
    params: dict = {"tag_id": 864, "active": "true", "closed": "false", "limit": limit}

    try:
        async with _client() as c:
            resp = await c.get(_gamma_url("/events"), params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        _cache["last_error"] = f"Gamma HTTP {e.response.status_code}: {e.response.text[:60]}"
        logger.warning(f"Polymarket /events HTTP {e.response.status_code}")
        return []
    except Exception as e:
        _cache["last_error"] = f"Gamma {type(e).__name__}: {e}"
        logger.warning(f"Polymarket /events error: {e}")
        return []

    events = data if isinstance(data, list) else data.get("data", [])
    markets: list[dict] = []

    for event in events:
        event_slug = event.get("slug", "")
        for item in event.get("markets", []):
            # Attach event slug so fetch_match_price can build the correct URL
            item = dict(item)
            item["_event_slug"] = event_slug
            # Populate homeTeam/awayTeam into question field if question is missing
            if not item.get("question") and (item.get("homeTeam") or item.get("awayTeam")):
                item["question"] = f"{item.get('homeTeam', '')} vs {item.get('awayTeam', '')}"
            markets.append(item)

    return markets


async def _refresh_cache() -> list[dict]:
    """Refresh market cache using /events?tag_id=864."""
    global _cache

    markets = await _fetch_tennis_events(limit=100)
    if markets:
        logger.info(f"Polymarket cache: {len(markets)} markets (tag_id=864, /events)")
    else:
        logger.warning("Polymarket cache: no tennis markets found via /events?tag_id=864")

    _cache["markets"] = markets
    _cache["fetched_at"] = time.time()
    return markets


async def _get_cached_markets() -> list[dict]:
    if time.time() - _cache["fetched_at"] > _CACHE_TTL or not _cache["markets"]:
        return await _refresh_cache()
    return _cache["markets"]


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


async def search_tennis_markets(player1: str, player2: str) -> list[dict]:
    """
    Find the Polymarket market for this matchup by searching the cached markets.
    Returns candidates sorted by name-match score.
    """
    all_markets = await _get_cached_markets()
    if not all_markets:
        return []

    scored = []
    for market in all_markets:
        # Use homeTeam/awayTeam directly when available (more reliable than parsing question)
        home = _normalize(market.get("homeTeam") or "")
        away = _normalize(market.get("awayTeam") or "")
        question = _normalize(market.get("question", "") or market.get("title", ""))

        if home and away:
            # Score against homeTeam/awayTeam fields first
            combined = f"{home} {away}"
            score = _name_score(combined, player1, player2)
        else:
            score = _name_score(question, player1, player2)

        if score >= 1.0:
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
            f"in {len(all_markets)} cached markets"
        )

    return [m for _, m in scored]


# ---------------------------------------------------------------------------
# Price extraction helpers
# ---------------------------------------------------------------------------

def _extract_gamma_price(market: dict, player_idx: int = 0) -> Optional[float]:
    """Extract win probability from a market's outcomePrices array."""
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
        if len(keys) > player_idx:
            try:
                return float(prices_raw[keys[player_idx]])
            except Exception:
                pass
    return None


def _extract_token_ids(market: dict) -> list[str]:
    """Extract CLOB token IDs from market dict (clobTokenIds field)."""
    raw = market.get("clobTokenIds") or market.get("clob_token_ids") or "[]"
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    return [str(t) for t in raw] if isinstance(raw, list) else []


def _find_player1_idx(market: dict, player1: str) -> int:
    """Determine which outcome index corresponds to player1."""
    outcomes_raw = market.get("outcomes") or "[]"
    if isinstance(outcomes_raw, str):
        try:
            outcomes = json.loads(outcomes_raw)
        except Exception:
            outcomes = []
    else:
        outcomes = list(outcomes_raw)

    last1 = _last_name(player1)
    for i, outcome_name in enumerate(outcomes):
        if last1 in _normalize(str(outcome_name)):
            return i
    return 0  # default: first outcome = player1


# ---------------------------------------------------------------------------
# Direct condition-ID price lookup (Gamma API)
# ---------------------------------------------------------------------------

async def fetch_price_by_condition_id(condition_id: str) -> Optional[float]:
    """Fetch P(player1 wins) from Gamma API for a known condition ID."""
    try:
        async with _client() as c:
            resp = await c.get(
                _gamma_url("/markets"),
                params={"condition_id": condition_id},
            )
            resp.raise_for_status()
            data = resp.json()
        market = data[0] if isinstance(data, list) and data else (data or {})
        return _extract_gamma_price(market, player_idx=0)
    except Exception as e:
        logger.debug(f"Gamma condition lookup {condition_id[:12]}...: {e}")
        return None


# ---------------------------------------------------------------------------
# Main entry point — fetch price for a match
# ---------------------------------------------------------------------------

async def fetch_match_price(
    player1: str,
    player2: str,
    condition_id: Optional[str] = None,
    token_id: Optional[str] = None,
) -> tuple[Optional[float], Optional[str], Optional[str], Optional[str]]:
    """
    Get P(player1 wins) from Polymarket.

    Returns (price, condition_id, slug, token_id):
      price        — float [0,1] P(player1 wins), or None
      condition_id — Polymarket condition ID
      slug         — market slug → https://polymarket.com/event/{slug}
      token_id     — CLOB token ID for player1's outcome (for fast future lookups)

    Strategy:
      1. If token_id known: try CLOB /prices (fastest, batch-able)
      2. If condition_id known: try Gamma API by condition_id
      3. Auto-discover via cached market search
    """
    # Strategy 1: CLOB prices via token ID (fastest, learned from weather-arb-bot)
    if token_id:
        prices = await fetch_clob_prices([token_id])
        if token_id in prices:
            return prices[token_id], condition_id, None, token_id

    # Strategy 2: Gamma API direct condition lookup
    if condition_id:
        price = await fetch_price_by_condition_id(condition_id)
        return price, condition_id, None, token_id

    # Strategy 3: Auto-discover via cached market search
    markets = await search_tennis_markets(player1, player2)
    if not markets:
        return None, None, None, None

    best = markets[0]
    cid = best.get("conditionId") or best.get("condition_id")
    # Use event slug (from parent event) for the correct market URL
    slug = best.get("_event_slug") or best.get("slug") or best.get("groupSlug")

    player1_idx = _find_player1_idx(best, player1)
    token_ids = _extract_token_ids(best)
    p1_token = token_ids[player1_idx] if len(token_ids) > player1_idx else None

    # Try CLOB first (real-time price)
    if p1_token:
        prices = await fetch_clob_prices([p1_token])
        if p1_token in prices:
            return prices[p1_token], cid, slug, p1_token

    # Fall back to Gamma outcomePrices
    price = _extract_gamma_price(best, player_idx=player1_idx)
    return price, cid, slug, p1_token


# ---------------------------------------------------------------------------
# Batch price update — fetch ALL live match prices in one CLOB call
# ---------------------------------------------------------------------------

async def batch_fetch_prices(token_map: dict[str, str]) -> dict[str, float]:
    """
    Fetch prices for multiple matches in a single CLOB API call.

    Args:
      token_map: {match_external_id: clob_token_id}

    Returns:
      {match_external_id: price_float}
    """
    if not token_map:
        return {}

    token_to_ext: dict[str, str] = {v: k for k, v in token_map.items()}
    prices = await fetch_clob_prices(list(token_map.values()))

    return {
        token_to_ext[token]: price
        for token, price in prices.items()
        if token in token_to_ext
    }


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

async def test_connectivity() -> dict:
    """Test Polymarket API connectivity. Called from /polytest command."""
    from app.config import settings
    proxy = settings.polymarket_proxy_url
    relay = settings.polymarket_relay_url

    result: dict = {
        "proxy_configured": bool(proxy),
        "relay_configured": bool(relay),
        "relay_hint": (relay[:50] + "...") if len(relay) > 50 else relay or "not set",
        "proxy_hint": (proxy[:30] + "...") if proxy else "not set",
        "ok": False,
        "http_status": None,
        "markets_found": 0,
        "sample_question": None,
        "last_cache_error": _cache.get("last_error", ""),
        "endpoint": _gamma_url("/events"),
    }

    try:
        async with _client(timeout=15.0) as c:
            resp = await c.get(
                _gamma_url("/events"),
                params={"tag_id": 864, "active": "true", "closed": "false", "limit": 5},
            )
            result["http_status"] = resp.status_code
            if resp.status_code == 200:
                data = resp.json()
                events = data if isinstance(data, list) else data.get("data", [])
                # Count individual markets across all events
                all_markets = [m for e in events for m in e.get("markets", [])]
                result["ok"] = True
                result["markets_found"] = len(all_markets)
                if all_markets:
                    m = all_markets[0]
                    q = m.get("question") or f"{m.get('homeTeam','')} vs {m.get('awayTeam','')}"
                    result["sample_question"] = q[:80]
    except Exception as e:
        result["http_status"] = f"{type(e).__name__}: {e}"

    return result
