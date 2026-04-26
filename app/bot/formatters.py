"""Telegram message formatters for opportunities and status updates."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional


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
    surface: str,
    tournament: str,
    notes: str = "",
) -> str:
    category_emoji = {"STRONG": "🔴", "MODERATE": "🟡", "WEAK": "🟢"}.get(edge_category, "⚪")
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")

    lines = [
        f"{category_emoji} *TENNIS EDGE DETECTED* {category_emoji}",
        f"",
        f"📍 {tournament} | {surface.capitalize()}",
        f"🎾 *{match_name}*",
        f"📊 Score: `{score_text}`",
        f"",
        f"▶️ Back: *{back_player}*",
        f"",
        f"📐 *Probabilities*",
        f"  TABLE model:  `{table_prob*100:.1f}%` \\[empirical\\]",
        f"  MARKOV model: `{markov_prob*100:.1f}%` \\[Markov chain\\]",
        f"  Consensus:    `{consensus_prob*100:.1f}%`",
        f"  Polymarket:   `{poly_price*100:.1f}%`",
        f"",
        f"📈 *Edge: {edge_pp:+.1f}pp* | Band: {elo_band} | Agreement: {model_agreement:.1f}pp",
    ]

    if notes:
        lines.append(f"💬 _{notes}_")

    lines += [
        f"",
        f"⏰ Detected at {now}",
        f"⚠️ _Statistical baseline only. Check injury/surface before acting._",
    ]

    return "\n".join(lines)


def fmt_status(
    live_matches: int,
    opportunities_today: int,
    last_elo_refresh: Optional[str],
    bot_running: bool,
) -> str:
    status = "✅ Running" if bot_running else "❌ Stopped"
    return (
        f"*Tennis Arb Bot Status*\n\n"
        f"Status: {status}\n"
        f"Live matches tracked: `{live_matches}`\n"
        f"Opportunities today: `{opportunities_today}`\n"
        f"Last ELO refresh: `{last_elo_refresh or 'never'}`\n"
    )


def fmt_match_prob(
    match_name: str,
    score_text: str,
    table_prob: float,
    markov_prob: float,
    consensus_prob: float,
    poly_price: Optional[float],
    elo_band: str,
    surface: str,
) -> str:
    poly_line = (
        f"Polymarket: `{poly_price*100:.1f}%` | Edge: `{(consensus_prob - poly_price)*100:+.1f}pp`"
        if poly_price is not None
        else "Polymarket: _not linked_"
    )
    return (
        f"*{match_name}*\n"
        f"Score: `{score_text}` | {surface.capitalize()} | Band: {elo_band}\n\n"
        f"TABLE:  `{table_prob*100:.1f}%`\n"
        f"MARKOV: `{markov_prob*100:.1f}%`\n"
        f"Consensus: `{consensus_prob*100:.1f}%`\n"
        f"{poly_line}"
    )
