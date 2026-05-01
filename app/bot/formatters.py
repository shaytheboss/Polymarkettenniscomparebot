"""
Telegram message formatters.
All messages use MarkdownV2 — special chars must be escaped with backslash.
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

    # Poly price display
    poly_display = last_trade_price if last_trade_price is not None else poly_price
    poly_label = "עסקה אחרונה" if last_trade_price is not None else "Polymarket"

    lines = [
        # ── Header ──────────────────────────────────────
        f"{cat_emoji} *{_esc(edge_category)} EDGE* {cat_emoji}",
        f"{tour_emoji} {_esc(tournament)} \\| {surface_emoji} {_esc(surface.capitalize())}",
        f"🎾 *{_esc(match_name)}*  \\|  `{_esc(score_text)}`",
        f"",
        # ── THE ACTION — most prominent ─────────────────
        f"━━━━━━━━━━━━━━━━━━━━━",
        f"🎯 *הימר על: {_esc(back_player)}*",
        f"   קנה ב\\-Polymarket: `{poly_display*100:.1f}%`  →  אנחנו: `{consensus_prob*100:.1f}%`",
        f"   Edge: `{edge_pp:+.1f}pp`  \\|  Kelly: `{kelly:.1%}`",
        f"━━━━━━━━━━━━━━━━━━━━━",
        f"",
        # ── Model breakdown ─────────────────────────────
        f"📐 *ניתוח שני המודלים:*",
        f"  TABLE `{table_prob*100:.0f}%` — טבלאות ניצחון מ\\-50K משחקים היסטוריים",
        f"  MARKOV `{markov_prob*100:.0f}%` — סימולציית נקודה\\-אחר\\-נקודה לפי ELO",
        f"  ✅ Consensus `{consensus_prob*100:.0f}%` — {'✅ שני מודלים מסכימים' if agree_ok else '⚠️ מודלים חלוקים — פחות בטוח'}",
        f"",
        f"  {_esc(poly_label)}: `{poly_display*100:.1f}%`"
        + (f"  _\\(mid: {poly_price*100:.1f}%\\)_" if last_trade_price is not None else ""),
    ]

    if edge_type_line:
        lines += [
            f"",
            f"🔍 {_esc(edge_type_line)}",
        ]

    if table_notes:
        lines += [f"💬 _{_esc(table_notes)}_"]

    lines += [
        f"",
        f"  פער ELO: `{elo_gap:+.0f}` \\({_esc(elo_band)}\\)  \\|  ⏰ {_esc(now)}",
    ]

    if polymarket_url:
        lines += [f"🔗 [פתח ב\\-Polymarket]({polymarket_url})"]

    lines += [f"⚠️ _סטטיסטיקה בלבד\\. לא המלצת השקעה\\._"]

    return "\n".join(lines)


def _classify_edge_type(
    score_text: str, p1_sets: int, p2_sets: int, elo_band: str, surface: str
) -> str:
    """Identify which of the 3 documented edges this might be."""
    if elo_band in ("E2", "E3") and p1_sets == 0 and p2_sets == 1:
        grass_warn = " ⚠️ משטח דשא מפחית comeback ב-3-5pp" if surface == "grass" else ""
        return f"SET1_LOSS_HEAVY_FAV — מועדף כבד הפסיד סט 1. שוק מגיב יתר על המידה.{grass_warn}"
    if "5-4" in score_text and p1_sets == 1 and p2_sets == 1:
        return "SERVING_FOR_MATCH — מגיש למשחק. שוק מתמחר 'choke' שלא קיים סטטיסטית."
    if p1_sets == 0 and p2_sets == 0:
        return "PRE-MATCH EDGE — פער ELO לא משתקף במחיר Polymarket."
    return ""


def _kelly_fraction(prob: float, price: float) -> float:
    """Full Kelly: (p*(1/price) - 1) / (1/price - 1), clamped to [0, 0.25]."""
    if price <= 0 or price >= 1:
        return 0.0
    b = (1.0 / price) - 1.0  # decimal odds - 1
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
    status = "✅ פועל" if bot_running else "❌ מופסק"
    return (
        f"*Tennis Arb Bot — Status*\n\n"
        f"סטטוס: {status}\n\n"
        f"*Live עכשיו*\n"
        f"  משחקים מעוקבים: `{live_matches}`\n\n"
        f"*היום*\n"
        f"  הזדמנויות שזוהו: `{opportunities_today}`\n"
        f"  חזקות \\(≥15pp\\): `{strong_today}`\n\n"
        f"*נתוני ELO*\n"
        f"  שחקני ATP: `{atp_players}`\n"
        f"  שחקני WTA: `{wta_players}`\n"
        f"  עדכון אחרון: `{_esc(last_elo_refresh or 'טרם בוצע')}`\n\n"
        f"פקודות: /live /opps /settings /help"
    )


# ---------------------------------------------------------------------------
# Live match probability card
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
) -> str:
    elo_gap = p1_elo - p2_elo
    surface_emoji = {"hard": "🔵", "clay": "🟤", "grass": "🟢"}.get(surface, "⚫")

    poly_lines = ""
    edge_line = ""
    if poly_price_p1 is not None:
        # Use last trade price for edge calculation when available (more accurate)
        ref_price = last_trade_p1 if last_trade_p1 is not None else poly_price_p1
        edge = (consensus_prob_p1 - ref_price) * 100
        edge_emoji = "🔥" if abs(edge) >= 8 else ("⚡" if abs(edge) >= 5 else "")
        if last_trade_p1 is not None:
            poly_lines = (
                f"  עסקה אחרונה: `{last_trade_p1*100:.1f}%`\n"
                f"  Mid price:   `{poly_price_p1*100:.1f}%`"
            )
        else:
            poly_lines = f"  Polymarket:  `{poly_price_p1*100:.1f}%`"
        edge_line = f"  Edge: `{edge:+.1f}pp` {edge_emoji}"
    else:
        poly_lines = "  Polymarket:  _לא מקושר_"

    return (
        f"🎾 *{_esc(match_name)}*\n"
        f"{surface_emoji} {_esc(surface.capitalize())} \\| {_esc(tour)} \\| Band: `{_esc(elo_band)}`\n"
        f"Score: `{_esc(score_text)}`\n\n"
        f"*{_esc(p1_name)}* ELO `{p1_elo:.0f}` vs *{_esc(p2_name)}* ELO `{p2_elo:.0f}`\n"
        f"פער: `{elo_gap:+.0f}`\n\n"
        f"📐 *האלגוריתם שלנו — P\\({_esc(p1_name.split()[0])} מנצח\\)*\n"
        f"  TABLE:       `{table_prob_p1*100:.1f}%`\n"
        f"  MARKOV:      `{markov_prob_p1*100:.1f}%`\n"
        f"  ✅ Consensus: `{consensus_prob_p1*100:.1f}%`\n\n"
        f"📉 *Polymarket*\n"
        f"{poly_lines}\n"
        f"{edge_line}\n"
        + (f"\n💬 _{_esc(table_notes)}_" if table_notes else "")
    )


# ---------------------------------------------------------------------------
# Help message
# ---------------------------------------------------------------------------

def fmt_help() -> str:
    return (
        "*Tennis Arb Bot — עזרה*\n\n"
        "*פקודות זמינות:*\n"
        "/start — הרשמה לבוט\n"
        "/status — סטטוס כללי\n"
        "/live — משחקים חיים עם הסתברויות\n"
        "/opps — הזדמנויות היום\n"
        "/settings — הגדרות התראות שלך\n"
        "/set\\_edge 8 — פער מינימלי לקבלת התראה \\(pp\\)\n"
        "/set\\_tours ATP — סינון ATP/WTA/ALL\n"
        "/refresh — סריקה מיידית של ESPN\n"
        "/track שם — חפש שחקן ב\\-ESPN\n"
        "/polytest — בדוק חיבור ל\\-Polymarket\n"
        "/setpoly שחקן condition\\_id — קשר שוק Polymarket ידנית\n"
        "/help — הודעה זו\n\n"
        "*מה הבוט עושה:*\n"
        "• עוקב אחרי משחקי ATP/WTA חיים דרך ESPN\n"
        "• מחשב הסתברות ניצחון בשני מודלים:\n"
        "  ─ TABLE: טבלאות אמפיריות \\(Sackmann/O'Shannessy\\)\n"
        "  ─ MARKOV: שרשרת מרקוב point\\-by\\-point\n"
        "• משווה ל\\-Polymarket ומתריע על פערים\n"
        "• מעדכן ELO פעם ביום מ\\-Tennis Abstract\n\n"
        "*Edge categories:*\n"
        "🔴 STRONG ≥15pp\n"
        "🟡 MODERATE 8\\-15pp\n"
        "🟢 WEAK 5\\-8pp\n\n"
        "*3 Edges מתועדים:*\n"
        "1\\. מועדף כבד \\(E2/E3\\) הפסיד סט 1 — שוק מגיב יתר\n"
        "2\\. WTA re\\-break — שוק מתמחר momentum שלא קיים\n"
        "3\\. מגיש למשחק — שוק מתמחר choke שלא קיים לE2\\+\n\n"
        "⚠️ _סטטיסטיקה בלבד\\. לא המלצת השקעה\\._"
    )


# ---------------------------------------------------------------------------
# Opportunity list summary
# ---------------------------------------------------------------------------

def fmt_opps_list(opps: list[dict]) -> str:
    if not opps:
        return "אין הזדמנויות שזוהו היום עדיין\\."

    cat_emoji = {"STRONG": "🔴", "MODERATE": "🟡", "WEAK": "🟢"}
    lines = [f"*הזדמנויות היום \\({len(opps)}\\)*\n"]
    for o in opps:
        em = cat_emoji.get(o.get("edge_category", ""), "⚪")
        outcome = o.get("outcome", "")
        outcome_str = ""
        if outcome == "WIN":
            outcome_str = " ✅"
        elif outcome == "LOSS":
            outcome_str = " ❌"
        lines.append(
            f"{em} *{_esc(o['back_player_name'])}*{outcome_str}\n"
            f"  `{_esc(o.get('score_text',''))}` \\| edge `{o['edge_pp']:+.1f}pp`\n"
            f"  Cons `{o['consensus_prob']*100:.0f}%` vs Poly `{o['poly_price']*100:.0f}%`\n"
        )
    return "\n".join(lines)
