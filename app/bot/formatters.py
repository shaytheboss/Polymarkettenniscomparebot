"""
Telegram message formatters — all English, MarkdownV2.
Special chars must be escaped: _ * [ ] ( ) ~ ` > # + - = | { } . ! \
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
import re


def _esc(text: str) -> str:
    """Escape MarkdownV2 special characters."""
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!\\])', r'\\\1', str(text))


# ---------------------------------------------------------------------------
# Opportunity alert — the main alert message
# ---------------------------------------------------------------------------

def fmt_opportunity(
    match_name: str,
    score_text: str,
    back_player: str,
    table_prob: float,
    markov_prob: float,
    consensus_prob: float,
    poly_price: float,
    edge_pp: float,
    model_agreement: float,
    edge_category: str,
    elo_band: str,
    elo_gap: float,
    surface: str,
    tournament: str,
    tour: str,
    table_notes: str = "",
    p1_sets: int = 0,
    p2_sets: int = 0,
    p1_games: int = 0,
    p2_games: int = 0,
    polymarket_url: str = "",
    last_trade_price: Optional[float] = None,
) -> str:
    cat_emoji = {"STRONG": "🔴", "MODERATE": "🟡", "WEAK": "🟢"}.get(edge_category, "⚪")
    surface_emoji = {"hard": "🔵", "clay": "🟤", "grass": "🟢"}.get(surface, "⚫")
    tour_emoji = "👨" if tour == "ATP" else "👩"
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")

    edge_type_line = _classify_edge_type(score_text, p1_sets, p2_sets, elo_band, surface)
    kelly = _kelly_fraction(consensus_prob, poly_price)
    agree_ok = model_agreement < 10

    # Use ask price when available (actual buying price), otherwise mid
    poly_display = last_trade_price if last_trade_price is not None else poly_price
    price_source = "ask" if last_trade_price is not None else "mid"

    lines = [
        f"{cat_emoji} *{_esc(edge_category)} EDGE* {cat_emoji}",
        f"{tour_emoji} {_esc(tournament)} \\| {surface_emoji} {_esc(surface.capitalize())}",
        f"🎾 *{_esc(match_name)}*",
        f"📊 Score: `{_esc(score_text)}`",
        f"",
        f"━━━━━━━━━━━━━━━━━━━━━",
        f"🎯 *BACK: {_esc(back_player)}*",
        f"",
        f"📉 Polymarket \\({_esc(price_source)}\\):  `{poly_display*100:.1f}%`",
        f"📈 Our model:    `{consensus_prob*100:.1f}%`",
        f"➡️ Edge:          `{edge_pp:+.1f}pp`",
        f"💰 Kelly:         `{kelly:.1%}`",
        f"━━━━━━━━━━━━━━━━━━━━━",
        f"",
        f"📐 *Model breakdown:*",
        f"  TABLE:      `{table_prob*100:.0f}%`  empirical tables \\(50K matches\\)",
        f"  MARKOV:     `{markov_prob*100:.0f}%`  point\\-by\\-point via ELO",
        f"  Consensus:  `{consensus_prob*100:.0f}%`  {'✅ models agree' if agree_ok else '⚠️ models diverge'}",
    ]

    if edge_type_line:
        lines += [f"", f"🔍 {_esc(edge_type_line)}"]

    if table_notes:
        lines += [f"💬 _{_esc(table_notes)}_"]

    lines += [
        f"",
        f"  ELO gap: `{elo_gap:+.0f}` \\({_esc(elo_band)}\\)  \\|  ⏰ {_esc(now)}",
    ]

    if polymarket_url:
        lines += [f"🔗 [Open on Polymarket]({polymarket_url})"]

    lines += [f"⚠️ _Statistical signal only\\. Not investment advice\\._"]

    return "\n".join(lines)


def _classify_edge_type(
    score_text: str, p1_sets: int, p2_sets: int, elo_band: str, surface: str
) -> str:
    if elo_band in ("E2", "E3") and p1_sets == 0 and p2_sets == 1:
        grass_warn = " ⚠️ Grass reduces comeback by 3\\-5pp" if surface == "grass" else ""
        return f"SET1\\_LOSS\\_HEAVY\\_FAV — Heavy favourite lost set 1\\. Market overreacting\\.{grass_warn}"
    if "5-4" in score_text and p1_sets == 1 and p2_sets == 1:
        return "SERVING\\_FOR\\_MATCH — Serving for match\\. Market pricing choke that stats don't support\\."
    if p1_sets == 0 and p2_sets == 0:
        return "PRE\\-MATCH EDGE — ELO gap not reflected in Polymarket price\\."
    return ""


def _kelly_fraction(prob: float, price: float) -> float:
    if price <= 0 or price >= 1:
        return 0.0
    b = (1.0 / price) - 1.0
    q = 1.0 - prob
    kelly = (prob * b - q) / b
    return max(0.0, min(0.25, kelly))


# ---------------------------------------------------------------------------
# Status message
# ---------------------------------------------------------------------------

def fmt_status(
    live_matches: int,
    opportunities_today: int,
    strong_today: int,
    last_elo_refresh: Optional[str],
    bot_running: bool,
    atp_players: int = 0,
    wta_players: int = 0,
) -> str:
    status = "✅ Running" if bot_running else "❌ Stopped"
    return (
        f"*Tennis Arb Bot — Status*\n\n"
        f"Status: {status}\n\n"
        f"*Live now*\n"
        f"  Tracked matches: `{live_matches}`\n\n"
        f"*Today*\n"
        f"  Opportunities: `{opportunities_today}`\n"
        f"  Strong \\(≥15pp\\): `{strong_today}`\n\n"
        f"*ELO data*\n"
        f"  ATP players: `{atp_players}`\n"
        f"  WTA players: `{wta_players}`\n"
        f"  Last refresh: `{_esc(last_elo_refresh or 'never')}`\n\n"
        f"Commands: /live /opps /settings /help"
    )


# ---------------------------------------------------------------------------
# Live match probability card  (/live command)
# ---------------------------------------------------------------------------

def fmt_match_prob(
    match_name: str,
    score_text: str,
    p1_name: str,
    p2_name: str,
    p1_elo: float,
    p2_elo: float,
    table_prob_p1: float,
    markov_prob_p1: float,
    consensus_prob_p1: float,
    poly_price_p1: Optional[float],
    elo_band: str,
    surface: str,
    tour: str,
    table_notes: str = "",
    last_trade_p1: Optional[float] = None,
    last_trade_p2: Optional[float] = None,
    poly_slug: Optional[str] = None,
    poly_condition_id: Optional[str] = None,
    poly_volume_24h: Optional[float] = None,
) -> str:
    elo_gap = p1_elo - p2_elo
    surface_emoji = {"hard": "🔵", "clay": "🟤", "grass": "🟢"}.get(surface, "⚫")
    p1_short = p1_name.split()[-1]
    p2_short = p2_name.split()[-1]

    if poly_price_p1 is not None:
        ref = last_trade_p1 if last_trade_p1 is not None else poly_price_p1
        edge = (consensus_prob_p1 - ref) * 100
        edge_emoji = "🔥" if abs(edge) >= 8 else ("⚡" if abs(edge) >= 5 else "")
        price_label = "ask" if last_trade_p1 is not None else "mid"
        # Per-player yes prices: p2 = 1 - p1 in a binary market
        yes_p1 = poly_price_p1
        yes_p2 = 1.0 - poly_price_p1
        lt2 = last_trade_p2 if last_trade_p2 is not None else (
            (1.0 - last_trade_p1) if last_trade_p1 is not None else None
        )
        last_line = (
            f"  Last trade: `{_esc(p1_short)}={last_trade_p1*100:.1f}% `\\| "
            f"`{_esc(p2_short)}={lt2*100:.1f}%`\n"
            if last_trade_p1 is not None and lt2 is not None else ""
        )
        vol_line = (
            f"  Volume \\(24h\\): `${poly_volume_24h:,.0f}`\n"
            if poly_volume_24h is not None and poly_volume_24h > 0 else ""
        )
        # URL + condition id always last (they're long and noisy)
        url_line = ""
        if poly_slug:
            poly_url = f"https://polymarket.com/event/{poly_slug}"
            url_line = f"  🔗 [Open market]({_esc(poly_url)})\n"
        cid_line = (
            f"  Condition: `{_esc(poly_condition_id[:14])}…`\n"
            if poly_condition_id and len(poly_condition_id) > 14 else
            (f"  Condition: `{_esc(poly_condition_id)}`\n" if poly_condition_id else "")
        )
        poly_section = (
            f"📉 *Polymarket*\n"
            f"  Yes price \\({price_label}\\): `{_esc(p1_short)}={yes_p1*100:.1f}% `\\| "
            f"`{_esc(p2_short)}={yes_p2*100:.1f}%`\n"
            f"{last_line}"
            f"{vol_line}"
            f"  Edge \\({_esc(p1_short)}\\): `{edge:+.1f}pp` {edge_emoji}\n"
            f"{url_line}"
            f"{cid_line}"
        ).rstrip()
    else:
        poly_section = "📉 *Polymarket*\n  Not linked yet"
        if poly_slug:
            poly_url = f"https://polymarket.com/event/{poly_slug}"
            poly_section += f"\n  🔗 [Open market]({_esc(poly_url)})"

    return (
        f"🎾 *{_esc(match_name)}*\n"
        f"{surface_emoji} {_esc(surface.capitalize())} \\| {_esc(tour)} \\| Band: `{_esc(elo_band)}`\n"
        f"Score: `{_esc(score_text)}`\n\n"
        f"*{_esc(p1_name)}* ELO `{p1_elo:.0f}` vs *{_esc(p2_name)}* ELO `{p2_elo:.0f}`\n"
        f"Gap: `{elo_gap:+.0f}`\n\n"
        f"📐 *Our model — P\\({_esc(p1_short)} wins\\)*\n"
        f"  TABLE:       `{table_prob_p1*100:.1f}%`\n"
        f"  MARKOV:      `{markov_prob_p1*100:.1f}%`\n"
        f"  ✅ Consensus: `{consensus_prob_p1*100:.1f}%`\n\n"
        f"{poly_section}\n"
        + (f"\n💬 _{_esc(table_notes)}_" if table_notes else "")
    )


# ---------------------------------------------------------------------------
# Help message
# ---------------------------------------------------------------------------

def fmt_help() -> str:
    return (
        "*Tennis Arb Bot — Help*\n\n"
        "*Commands:*\n"
        "/start — Register for alerts\n"
        "/status — Bot status overview\n"
        "/live — Live matches with probabilities\n"
        "/opps — Today's opportunities\n"
        "/settings — Your alert settings\n"
        "/set\\_edge 8 — Min edge to receive alert \\(pp\\)\n"
        "/set\\_min\\_prob 70 — Min consensus probability \\(0 = all\\)\n"
        "/set\\_tours ATP — Filter ATP / WTA / ALL\n"
        "/refresh — Force ESPN scan now\n"
        "/track Name — Find player in ESPN\n"
        "/polytest — Test Polymarket connectivity\n"
        "/setpoly Player condition\\_id — Manually link Polymarket market\n"
        "/help — This message\n\n"
        "*What the bot does:*\n"
        "• Tracks live ATP/WTA matches via ESPN\n"
        "• Calculates win probability using two models:\n"
        "  ─ TABLE: empirical tables \\(Sackmann/O'Shannessy\\)\n"
        "  ─ MARKOV: point\\-by\\-point Markov chain\n"
        "• Compares to Polymarket and alerts on mispricing\n"
        "• Updates ELO once a day from Tennis Abstract\n\n"
        "*Edge categories:*\n"
        "🔴 STRONG ≥15pp\n"
        "🟡 MODERATE 8\\-15pp\n"
        "🟢 WEAK 5\\-8pp\n\n"
        "*3 documented edges:*\n"
        "1\\. Heavy favourite \\(E2/E3\\) lost set 1 — market overreacts\n"
        "2\\. WTA re\\-break — market prices momentum that doesn't exist\n"
        "3\\. Serving for match — market prices a choke not supported by data\n\n"
        "⚠️ _Statistical signal only\\. Not investment advice\\._"
    )


# ---------------------------------------------------------------------------
# Opportunity list summary  (/opps command)
# ---------------------------------------------------------------------------

def fmt_opps_list(opps: list[dict]) -> str:
    if not opps:
        return "No opportunities detected today yet\\."

    cat_emoji = {"STRONG": "🔴", "MODERATE": "🟡", "WEAK": "🟢"}
    lines = [f"*Today's opportunities \\({len(opps)}\\)*\n"]
    for o in opps:
        em = cat_emoji.get(o.get("edge_category", ""), "⚪")
        outcome = o.get("outcome", "")
        outcome_str = " ✅ WIN" if outcome == "WIN" else (" ❌ LOSS" if outcome == "LOSS" else "")
        lines.append(
            f"{em} *{_esc(o['back_player_name'])}*{outcome_str}\n"
            f"  `{_esc(o.get('score_text',''))}` \\| edge `{o['edge_pp']:+.1f}pp`\n"
            f"  Cons `{o['consensus_prob']*100:.0f}%` vs Poly `{o['poly_price']*100:.0f}%`\n"
        )
    return "\n".join(lines)
