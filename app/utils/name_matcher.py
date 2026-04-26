"""
Fuzzy player name matcher.

The problem: three data sources each use different name formats:
  ESPN live:      "Novak Djokovic", "Carlos Alcaraz"
  Tennis Abstract: "Novak Djokovic", "N. Djokovic", or "Djokovic N."
  Polymarket:     "Djokovic", "N. Djokovic", "Novak Djokovic"

Strategy (in priority order):
  1. Exact match (case-insensitive)
  2. Last-name exact match (last token)
  3. Normalized last-name match (strip accents, lowercase)
  4. Abbreviation match: "N. Djokovic" ↔ "Novak Djokovic"
  5. Fuzzy sequence ratio on last name (threshold configurable)
"""
from __future__ import annotations
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Optional


def _strip_accents(s: str) -> str:
    """Remove diacritics: é→e, ü→u, etc."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _normalize(name: str) -> str:
    """Lowercase, strip accents, collapse spaces, remove punctuation except hyphens."""
    name = _strip_accents(name.lower())
    name = re.sub(r"[^a-z0-9 \-]", "", name)
    return re.sub(r"\s+", " ", name).strip()


def _last_name(name: str) -> str:
    """
    Extract last name from various formats:
      "Novak Djokovic"   → "djokovic"
      "N. Djokovic"      → "djokovic"
      "Djokovic, Novak"  → "djokovic"
      "Djokovic"         → "djokovic"
    """
    n = _normalize(name)
    # Handle "Last, First" format
    if "," in n:
        n = n.split(",")[0].strip()
    parts = n.split()
    if not parts:
        return ""
    # Filter out single-letter initials ("n.") already cleaned
    non_initial = [p for p in parts if len(p) > 1]
    return non_initial[-1] if non_initial else parts[-1]


def _first_initial(name: str) -> str:
    """Extract first initial: "Novak Djokovic" → "n", "N. Djokovic" → "n"."""
    n = _normalize(name)
    if "," in n:
        parts = [p.strip() for p in n.split(",")]
        first_part = parts[1] if len(parts) > 1 else parts[0]
    else:
        first_part = n
    tokens = first_part.split()
    return tokens[0][0] if tokens else ""


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def match_name(
    query: str,
    candidates: list[str],
    threshold: float = 0.80,
) -> Optional[str]:
    """
    Find the best matching name in candidates for query.
    Returns the matched candidate string or None if no match above threshold.

    threshold: minimum fuzzy ratio on last-name (0-1). 0.80 means 80% similarity.
    """
    if not candidates:
        return None

    q_norm = _normalize(query)
    q_last = _last_name(query)
    q_init = _first_initial(query)

    # 1. Exact match
    for c in candidates:
        if _normalize(c) == q_norm:
            return c

    # 2. Last-name exact
    last_exact = [c for c in candidates if _last_name(c) == q_last]
    if len(last_exact) == 1:
        return last_exact[0]
    if len(last_exact) > 1:
        # Disambiguate by first initial
        init_match = [c for c in last_exact if _first_initial(c) == q_init]
        if init_match:
            return init_match[0]
        return last_exact[0]

    # 3. Fuzzy last-name match
    scored = [
        (c, _ratio(q_last, _last_name(c)))
        for c in candidates
        if _ratio(q_last, _last_name(c)) >= threshold
    ]
    if scored:
        scored.sort(key=lambda x: x[1], reverse=True)
        best_c, best_score = scored[0]
        # If multiple candidates have same high score, use first initial to disambiguate
        if len(scored) > 1 and scored[0][1] == scored[1][1]:
            init_match = [c for c, _ in scored if _first_initial(c) == q_init]
            if init_match:
                return init_match[0]
        return best_c

    # 4. Full-name fuzzy fallback (lower threshold)
    full_scored = [
        (c, _ratio(q_norm, _normalize(c)))
        for c in candidates
    ]
    full_scored.sort(key=lambda x: x[1], reverse=True)
    if full_scored and full_scored[0][1] >= threshold * 0.85:
        return full_scored[0][0]

    return None


def best_match_score(query: str, candidates: list[str]) -> tuple[Optional[str], float]:
    """Return (best_candidate, similarity_score) without threshold cutoff."""
    if not candidates:
        return None, 0.0
    q_last = _last_name(query)
    scored = [(c, _ratio(q_last, _last_name(c))) for c in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[0]


def normalize_for_polymarket(name: str) -> list[str]:
    """
    Generate search variants for Polymarket query.
    Returns list of strings to try in order.
    """
    clean = name.strip()
    parts = clean.split()
    last = parts[-1] if parts else clean
    first = parts[0] if len(parts) > 1 else ""
    initial = first[0] if first else ""

    variants = [
        clean,                          # "Novak Djokovic"
        last,                           # "Djokovic"
        f"{initial}. {last}" if initial else last,  # "N. Djokovic"
        f"{last} {first}" if first else last,        # "Djokovic Novak"
    ]
    # Deduplicate while preserving order
    seen = set()
    result = []
    for v in variants:
        if v not in seen:
            seen.add(v)
            result.append(v)
    return result
