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

async def fetch_clob_prices(token_ids: list[str]) -> tuple[dict[str, float], bool]:
    """
    Fetch current prices for multiple token IDs.
    Returns (prices_dict, call_succeeded).

    Tries four formats in order until one works, logging which succeeded.
    The Polymarket CLOB V2 format is undocumented and has changed — this
    cascade ensures we survive API changes without a redeploy.
    """
    if not token_ids:
        return {}, True
    unique_ids = list(dict.fromkeys(token_ids))

    # --- Attempt 1: POST /prices with JSON body [{"token_id": X, "side": "BUY"}, ...] ---
    # CLOB V2 batch endpoint likely expects POST with JSON body, not GET with params.
    try:
        async with _client() as c:
            resp = await c.post(
                _clob_url("/prices"),
                json=[{"token_id": tid, "side": "BUY"} for tid in unique_ids],
            )
        if resp.status_code == 200:
            data = resp.json()
            prices = _parse_prices_response(data, unique_ids)
            if prices:
                logger.info(f"CLOB POST /prices: {len(prices)} prices (batch, attempt 1)")
                _cache["last_error"] = ""
                return prices, True
            # 200 but empty — tokens might be stale; signal batch worked
            logger.debug("CLOB POST /prices: 200 but no matching prices in response")
            return {}, True
        logger.debug(f"CLOB POST /prices: {resp.status_code} {resp.text[:60]}")
    except Exception as e:
        logger.debug(f"CLOB POST /prices attempt 1 failed: {e}")

    # --- Attempt 2: GET /prices with interleaved side=BUY per token ---
    # Format: ?token_id=X&side=BUY&token_id=Y&side=BUY
    try:
        async with _client() as c:
            params = []
            for tid in unique_ids:
                params.extend([("token_id", tid), ("side", "BUY")])
            resp = await c.get(_clob_url("/prices"), params=params)
        if resp.status_code == 200:
            prices = _parse_prices_response(resp.json(), unique_ids)
            if prices:
                logger.info(f"CLOB GET /prices interleaved: {len(prices)} prices (attempt 2)")
                _cache["last_error"] = ""
                return prices, True
            return {}, True
        logger.debug(f"CLOB GET /prices interleaved: {resp.status_code} {resp.text[:60]}")
    except Exception as e:
        logger.debug(f"CLOB GET /prices attempt 2 failed: {e}")

    # --- Attempt 3: GET /prices with global side=BUY ---
    # Format: ?token_id=X&token_id=Y&side=BUY
    try:
        async with _client() as c:
            params = [("token_id", tid) for tid in unique_ids] + [("side", "BUY")]
            resp = await c.get(_clob_url("/prices"), params=params)
        if resp.status_code == 200:
            prices = _parse_prices_response(resp.json(), unique_ids)
            if prices:
                logger.info(f"CLOB GET /prices global-side: {len(prices)} prices (attempt 3)")
                _cache["last_error"] = ""
                return prices, True
            return {}, True
        body = resp.text[:80]
        _cache["last_error"] = f"CLOB HTTP {resp.status_code}: {body}"
        logger.warning(f"CLOB /prices all batch formats failed — last: {resp.status_code}: {body}")
    except Exception as e:
        _cache["last_error"] = f"CLOB {type(e).__name__}: {e}"
        logger.warning(f"CLOB /prices error: {e}")

    # --- Attempt 4: individual GET /price?token_id=X&side=BUY calls ---
    logger.info("CLOB batch failed — falling back to individual /price calls")
    return await _fetch_clob_prices_individual(unique_ids)


def _parse_prices_response(data, token_ids: list[str]) -> dict[str, float]:
    """
    Parse whatever format /prices returns into {token_id: price} dict.
    Handles: {"tid": "0.65"}, {"data": [...]}, [{"token_id": tid, "price": p}], etc.
    """
    result: dict[str, float] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            if v is not None:
                try:
                    result[k] = float(v)
                except (TypeError, ValueError):
                    pass
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                tid = item.get("token_id") or item.get("tokenId")
                price = item.get("price") or item.get("mid")
                if tid and price is not None:
                    try:
                        result[str(tid)] = float(price)
                    except (TypeError, ValueError):
                        pass
    return result


async def _fetch_clob_prices_individual(
    token_ids: list[str],
) -> tuple[dict[str, float], bool]:
    """
    Fetch prices one at a time when batch /prices fails.
    Tries two endpoints per token: /price?side=BUY, then /book (order book mid).
    """
    import asyncio as _asyncio

    async def _one(tid: str) -> tuple[str, Optional[float]]:
        # Try 1: GET /price?token_id=X&side=BUY
        try:
            async with _client() as c:
                resp = await c.get(
                    _clob_url("/price"),
                    params={"token_id": tid, "side": "BUY"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    raw = data.get("price") if isinstance(data, dict) else None
                    if raw is not None:
                        return tid, float(raw)
        except Exception as exc:
            logger.debug(f"CLOB /price?side=BUY failed for {tid[:16]}: {exc}")

        # Try 2: GET /book?token_id=X — extract mid from bids+asks (no side needed)
        try:
            async with _client() as c:
                resp = await c.get(_clob_url("/book"), params={"token_id": tid})
                if resp.status_code == 200:
                    book = resp.json()
                    bids = book.get("bids", [])
                    asks = book.get("asks", [])
                    if bids and asks:
                        best_bid = float(bids[0]["price"])
                        best_ask = min(float(a["price"]) for a in asks)
                        return tid, round((best_bid + best_ask) / 2, 4)
                    # last_trade_price embedded in book response (some API versions)
                    ltp = book.get("last_trade_price")
                    if ltp is not None:
                        return tid, float(ltp)
        except Exception as exc:
            logger.debug(f"CLOB /book fallback failed for {tid[:16]}: {exc}")

        return tid, None

    results = await _asyncio.gather(*[_one(tid) for tid in token_ids])
    prices = {tid: p for tid, p in results if p is not None}
    if prices:
        logger.info(f"CLOB individual prices: {len(prices)}/{len(token_ids)} tokens resolved")
        _cache["last_error"] = ""
    return prices, bool(prices) or not token_ids


async def fetch_last_trade_price(token_id: str) -> Optional[float]:
    """
    Fetch the last-trade price for a token. Three attempts in order:
      1. GET /last-trade-price  (dedicated endpoint)
      2. GET /book              (V2: last_trade_price embedded in response)
      3. Compute mid from best bid/ask in the book as final fallback
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

    # Fallback: order book (V2 embeds last_trade_price directly in the book response)
    try:
        async with _client() as c:
            resp = await c.get(
                _clob_url("/book"),
                params={"token_id": token_id},
            )
            resp.raise_for_status()
            book = resp.json()
        # V2: last_trade_price is a field on the book object
        ltp = book.get("last_trade_price")
        if ltp is not None:
            return float(ltp)
        # Final fallback: compute mid from best bid/ask
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        if bids and asks:
            best_bid = float(bids[0]["price"])
            best_ask = float(asks[0]["price"])
            return round((best_bid + best_ask) / 2, 4)
    except Exception as e:
        logger.debug(f"CLOB order book fallback failed for {token_id[:12]}: {e}")

    return None


async def fetch_best_ask(token_id: str) -> Optional[float]:
    """
    Fetch the best ask price for a token — the actual price you'd pay to buy YES.

    Returns None when:
    - The order book is empty (no active sellers)
    - The price is extreme (< 0.04 or > 0.96) — likely a stale/resolved market
    Does NOT fall back to last-trade-price to avoid stale data from finished matches.
    """
    try:
        async with _client() as c:
            resp = await c.get(
                _clob_url("/book"),
                params={"token_id": token_id},
            )
            resp.raise_for_status()
            book = resp.json()
        asks = book.get("asks", [])
        if asks:
            # Use min() — defensive against APIs that return asks in descending order
            best = min(float(a["price"]) for a in asks)
            if 0.04 <= best <= 0.96:
                return best
            logger.warning(
                f"fetch_best_ask: extreme ask {best:.3f} for {token_id[:16]} — "
                f"market likely resolved or stale, discarding"
            )
    except Exception as e:
        logger.debug(f"CLOB /book failed for {token_id[:12]}: {e}")
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
            item = dict(item)
            item["_event_slug"] = event_slug
            if not item.get("question") and (item.get("homeTeam") or item.get("awayTeam")):
                item["question"] = f"{item.get('homeTeam', '')} vs {item.get('awayTeam', '')}"
            # Only cache head-to-head match markets — skip tournament winner markets
            # (e.g., "Will Draper win the Australian Open?") which have no opponent
            if _is_match_market(item):
                markets.append(item)

    return markets


def _is_match_market(market: dict) -> bool:
    """Return True only for head-to-head match markets, not tournament winner markets."""
    home = (market.get("homeTeam") or "").strip()
    away = (market.get("awayTeam") or "").strip()
    if home and away:
        return True
    question = (market.get("question") or market.get("title") or "").lower()
    return " vs " in question or " v." in question or " v " in question


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

def _player_score(text: str, name: str) -> float:
    """Score 0–1 for how well a single player name appears in text."""
    last = _last_name(name)
    full = _normalize(name)
    if last in text:
        return 1.0
    if full in text:
        return 0.8
    if len(last) >= 4 and last[:4] in text:
        return 0.4
    return 0.0


def _name_score(question_norm: str, p1: str, p2: str) -> float:
    """Score 0–2: how well both player names appear in the question."""
    return _player_score(question_norm, p1) + _player_score(question_norm, p2)


async def search_tennis_markets(player1: str, player2: str) -> list[dict]:
    """
    Find the Polymarket market for this matchup by searching the cached markets.
    Returns candidates sorted by name-match score.

    BOTH players must appear in the market text — this prevents linking a
    tournament-winner market (or a different match that shares one player name)
    to the wrong live match.
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

        combined = f"{home} {away}" if (home and away) else question

        s1 = _player_score(combined, player1)
        s2 = _player_score(combined, player2)

        # Require BOTH players to have at least a partial match.
        # A score of 0 for either player means this is the wrong market
        # (tournament winner, or a different match that shares one player).
        if s1 > 0 and s2 > 0:
            scored.append((s1 + s2, market))

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


def _find_player1_idx(market: dict, player1: str, player2: str = "") -> int:
    """
    Determine which outcome index (0 or 1) corresponds to player1.

    Priority:
      1. homeTeam/awayTeam fields (explicit, reliable)
      2. outcomes[] name matching
      3. Default 0 with a warning
    """
    last1 = _last_name(player1)
    last2 = _last_name(player2) if player2 else ""

    # Priority 1: homeTeam/awayTeam — directly parallel to outcomes[0]/outcomes[1]
    home = _normalize(market.get("homeTeam") or "")
    away = _normalize(market.get("awayTeam") or "")
    if home and away:
        if last1 in home:
            logger.debug(f"_find_player1_idx: homeTeam match → idx=0 ({player1})")
            return 0
        if last1 in away:
            logger.debug(f"_find_player1_idx: awayTeam match → idx=1 ({player1})")
            return 1
        # Neither matched via homeTeam/awayTeam — fall through to outcomes

    # Priority 2: outcomes[] name matching
    outcomes_raw = market.get("outcomes") or "[]"
    if isinstance(outcomes_raw, str):
        try:
            outcomes = json.loads(outcomes_raw)
        except Exception:
            outcomes = []
    else:
        outcomes = list(outcomes_raw)

    for i, outcome_name in enumerate(outcomes):
        if last1 in _normalize(str(outcome_name)):
            logger.debug(f"_find_player1_idx: outcomes[{i}] match → {outcome_name}")
            return i

    # Default — log a warning so we can catch wrong assignments
    q = market.get("question") or market.get("title", "")
    logger.warning(
        f"_find_player1_idx: no outcome matched '{player1}' (last='{last1}') "
        f"in market '{q[:60]}' outcomes={outcomes[:4]} "
        f"homeTeam='{market.get('homeTeam','')}' awayTeam='{market.get('awayTeam','')}' "
        f"— defaulting to idx=0 (may be WRONG)"
    )
    return 0


# ---------------------------------------------------------------------------
# Direct condition-ID price lookup (Gamma API)
# ---------------------------------------------------------------------------

async def fetch_price_by_condition_id(
    condition_id: str,
    player1: str = "",
    player2: str = "",
) -> Optional[float]:
    """Fetch P(player1 wins) from Gamma API for a known condition ID.

    Player names are used to identify the correct outcome index (0 or 1).
    Without them we default to index 0 which may return the wrong player's price.
    """
    try:
        async with _client() as c:
            resp = await c.get(
                _gamma_url("/markets"),
                params={"condition_id": condition_id},
            )
            resp.raise_for_status()
            data = resp.json()
        market = data[0] if isinstance(data, list) and data else (data or {})

        # Validate: both players must appear in this market.
        # If only one (or neither) matches, the stored condition_id points to the wrong
        # market — return None so the caller can clear it and re-discover.
        if player1 and player2:
            home = _normalize(market.get("homeTeam") or "")
            away = _normalize(market.get("awayTeam") or "")
            question = _normalize(market.get("question", "") or market.get("title", ""))
            combined = f"{home} {away}" if (home and away) else question
            s1 = _player_score(combined, player1)
            s2 = _player_score(combined, player2)
            if s1 == 0 or s2 == 0:
                logger.warning(
                    f"Stored condition_id {condition_id[:12]} doesn't match both players "
                    f"({player1.split()[-1]} s={s1:.1f}, {player2.split()[-1]} s={s2:.1f}) "
                    f"market='{(question or combined)[:60]}' — clearing for re-discovery"
                )
                return None

        player_idx = _find_player1_idx(market, player1, player2) if player1 else 0
        price = _extract_gamma_price(market, player_idx=player_idx)
        if price is not None:
            logger.info(
                f"Gamma price (cid={condition_id[:12]}): "
                f"{player1.split()[-1] if player1 else 'P1'}={price*100:.1f}% "
                f"(idx={player_idx})"
            )
        return price
    except Exception as e:
        logger.warning(f"Gamma condition lookup {condition_id[:12]}: {e}")
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
        prices, _ = await fetch_clob_prices([token_id])
        if token_id in prices:
            return prices[token_id], condition_id, None, token_id

    # Strategy 2: Gamma API direct condition lookup (with correct player index)
    if condition_id:
        price = await fetch_price_by_condition_id(condition_id, player1, player2)
        return price, condition_id, None, token_id

    # Strategy 3: Auto-discover via cached market search
    markets = await search_tennis_markets(player1, player2)
    if not markets:
        return None, None, None, None

    best = markets[0]
    cid = best.get("conditionId") or best.get("condition_id")
    # Use event slug (from parent event) for the correct market URL
    slug = best.get("_event_slug") or best.get("slug") or best.get("groupSlug")

    player1_idx = _find_player1_idx(best, player1, player2)
    token_ids = _extract_token_ids(best)
    p1_token = token_ids[player1_idx] if len(token_ids) > player1_idx else None

    outcomes_raw = best.get("outcomes") or "[]"
    try:
        outcomes_list = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else list(outcomes_raw)
    except Exception:
        outcomes_list = []
    logger.info(
        f"Polymarket link: {player1} vs {player2} → "
        f"market='{best.get('question', best.get('title', ''))[:60]}' "
        f"homeTeam='{best.get('homeTeam','')}' awayTeam='{best.get('awayTeam','')}' "
        f"outcomes={outcomes_list[:4]} player1_idx={player1_idx} "
        f"p1_token={str(p1_token)[:16] if p1_token else None}"
    )

    # Try CLOB first (real-time price)
    if p1_token:
        prices, _ = await fetch_clob_prices([p1_token])
        if p1_token in prices:
            logger.info(
                f"Polymarket CLOB price: {player1} → {prices[p1_token]*100:.1f}% "
                f"(token idx={player1_idx})"
            )
            return prices[p1_token], cid, slug, p1_token

    # Fall back to Gamma outcomePrices
    price = _extract_gamma_price(best, player_idx=player1_idx)
    return price, cid, slug, p1_token


# ---------------------------------------------------------------------------
# Batch price update — fetch ALL live match prices in one CLOB call
# ---------------------------------------------------------------------------

async def batch_fetch_prices(token_map: dict[str, str]) -> tuple[dict[str, float], bool]:
    """
    Fetch prices for multiple matches in a single CLOB API call.

    Args:
      token_map: {match_external_id: clob_token_id}

    Returns:
      ({match_external_id: price_float}, call_succeeded)
      call_succeeded=True + empty dict means tokens are stale/invalid in CLOB (V2 upgrade).
      call_succeeded=False means a network/HTTP error — do NOT treat missing tokens as stale.
    """
    if not token_map:
        return {}, True

    token_to_ext: dict[str, str] = {v: k for k, v in token_map.items()}
    prices, ok = await fetch_clob_prices(list(token_map.values()))

    result = {
        token_to_ext[token]: price
        for token, price in prices.items()
        if token in token_to_ext
    }
    return result, ok


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

    # Test Gamma API
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
                all_markets = [m for e in events for m in e.get("markets", [])]
                result["ok"] = True
                result["markets_found"] = len(all_markets)
                if all_markets:
                    m = all_markets[0]
                    q = m.get("question") or f"{m.get('homeTeam','')} vs {m.get('awayTeam','')}"
                    result["sample_question"] = q[:80]
    except Exception as e:
        result["http_status"] = f"{type(e).__name__}: {e}"

    # Test CLOB API reachability via /markets (unauthenticated, low-cost check)
    try:
        async with _client(timeout=10.0) as c:
            resp = await c.get(_clob_url("/markets"), params={"limit": 1})
            result["clob_status"] = resp.status_code
            result["clob_ok"] = resp.status_code == 200
    except Exception as e:
        result["clob_status"] = f"{type(e).__name__}: {str(e)[:60]}"
        result["clob_ok"] = False

    # Test CLOB price fetch — try all 4 formats with a real token from Gamma cache.
    # This tells us exactly which format works so we know what to fix next.
    if result["clob_ok"] and result.get("ok"):
        test_token: Optional[str] = None
        try:
            async with _client(timeout=12.0) as c:
                resp2 = await c.get(
                    _gamma_url("/events"),
                    params={"tag_id": 864, "active": "true", "closed": "false", "limit": 3},
                )
                if resp2.status_code == 200:
                    events2 = resp2.json()
                    if isinstance(events2, dict):
                        events2 = events2.get("data", [])
                    for ev2 in events2:
                        for mkt in ev2.get("markets", []):
                            tokens = _extract_token_ids(mkt)
                            if tokens:
                                test_token = tokens[0]
                                break
                        if test_token:
                            break
        except Exception:
            pass

        if not test_token:
            result["clob_price_ok"] = None
        else:
            formats_tried: list[str] = []
            working_format: Optional[str] = None
            working_price: Optional[str] = None

            async with _client(timeout=10.0) as c:
                probe_tests = [
                    ("POST /prices JSON",     lambda: c.post(_clob_url("/prices"), json=[{"token_id": test_token, "side": "BUY"}])),
                    ("GET /prices+side=BUY",  lambda: c.get(_clob_url("/prices"), params=[("token_id", test_token), ("side", "BUY")])),
                    ("GET /price?side=BUY",   lambda: c.get(_clob_url("/price"),  params={"token_id": test_token, "side": "BUY"})),
                    ("GET /book (mid)",       lambda: c.get(_clob_url("/book"),   params={"token_id": test_token})),
                ]
                for fmt_name, call in probe_tests:
                    try:
                        r = await call()
                        formats_tried.append(f"{fmt_name}→{r.status_code}")
                        if r.status_code == 200:
                            body = r.json()
                            # /book: extract mid from bids/asks
                            if fmt_name.startswith("GET /book"):
                                bids = body.get("bids", [])
                                asks = body.get("asks", [])
                                if bids and asks:
                                    mid = (float(bids[0]["price"]) + min(float(a["price"]) for a in asks)) / 2
                                    working_price = f"{mid:.3f}"
                                    working_format = fmt_name
                                    break
                            else:
                                parsed = _parse_prices_response(body, [test_token])
                                if parsed:
                                    working_price = f"{list(parsed.values())[0]:.3f}"
                                    working_format = fmt_name
                                    break
                                # 200 but no prices — token not in CLOB
                                elif r.status_code == 200:
                                    working_format = f"{fmt_name} (200, no price for test token)"
                                    break
                    except Exception as exc:
                        formats_tried.append(f"{fmt_name}→ERR:{str(exc)[:30]}")

            result["clob_price_ok"] = working_format is not None
            result["clob_price_format"] = working_format or "none worked"
            result["clob_price_value"] = working_price
            result["clob_price_formats_tried"] = " | ".join(formats_tried)
            if not working_format:
                result["clob_price_error"] = "All 4 formats failed: " + " | ".join(formats_tried[-2:])

    return result
