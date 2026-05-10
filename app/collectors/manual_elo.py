"""
Manual ELO loader — reads user-curated TSV files from data/elo_*_manual.tsv.

Purpose: provide a second, independent ELO source so notifications can
display both the automatic ELO (computed from match data) and a curated
ELO copied from a reference site.  When the auto source is missing or
shows the 1500 stub, the manual ELO is used as a fallback.

File format (tab-separated, matches Tennis Abstract export verbatim):
  rank  name  age  elo  ""  hRank  hElo  cRank  cElo  gRank  gElo  ""  peakElo  peakMonth  ""  atpRank  logDiff

Lines starting with "#" are comments. Empty lines and the header row are
skipped. The user pastes table rows directly into the file and commits.
"""
from __future__ import annotations
import logging
import os
import threading
from pathlib import Path
from typing import Optional

from app.utils.name_matcher import _last_name, _normalize, match_name

logger = logging.getLogger(__name__)

# Project root: this file is at app/collectors/manual_elo.py, so go up two levels.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ATP_PATH = _REPO_ROOT / "data" / "elo_atp_manual.tsv"
WTA_PATH = _REPO_ROOT / "data" / "elo_wta_manual.tsv"

# Cache: tour → {normalized_name: row_dict}.  Reloaded when file mtime changes.
_cache: dict[str, dict] = {"ATP": {}, "WTA": {}}
_cache_mtime: dict[str, float] = {"ATP": 0.0, "WTA": 0.0}
_lock = threading.Lock()


def _path(tour: str) -> Path:
    return ATP_PATH if tour == "ATP" else WTA_PATH


def _parse_row(line: str) -> Optional[dict]:
    """
    Parse one row of the manual ELO TSV. Returns None for unparseable lines.

    Column layout after split('\\t'), with blank spacer columns kept:
      [0]rank  [1]name  [2]age  [3]elo  [4]<spacer>  [5]hRank  [6]hElo
      [7]cRank  [8]cElo  [9]gRank  [10]gElo  [11]<spacer>
      [12]peakElo  [13]peakMonth  [14]<spacer>  [15]atpRank  [16]logDiff
    """
    parts = [p.strip() for p in line.split("\t")]
    if len(parts) < 4:
        return None

    # Skip header row (contains the literal word "Player")
    if any(p.lower() == "player" for p in parts):
        return None

    # First column should be a numeric rank
    if not parts[0].isdigit():
        return None

    name = parts[1]
    if not name:
        return None

    def _f(idx: int) -> Optional[float]:
        if idx >= len(parts) or not parts[idx]:
            return None
        try:
            return float(parts[idx])
        except ValueError:
            return None

    def _i(idx: int) -> Optional[int]:
        if idx >= len(parts) or not parts[idx]:
            return None
        try:
            return int(parts[idx])
        except ValueError:
            return None

    elo = _f(3)
    if elo is None or not (1000 <= elo <= 3000):
        return None

    return {
        "name":       name,
        "elo":        elo,
        "elo_hard":   _f(6),
        "elo_clay":   _f(8),
        "elo_grass":  _f(10),
        "peak_elo":   _f(12),
        "peak_month": parts[13] if len(parts) > 13 else "",
        "ranking":    _i(15),
    }


def _load_file(tour: str) -> dict[str, dict]:
    """Read and parse the manual ELO file for tour. Returns {normalized_name: row}."""
    path = _path(tour)
    if not path.exists():
        logger.debug(f"Manual ELO file not found: {path}")
        return {}

    rows: dict[str, dict] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n").rstrip("\r")
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                row = _parse_row(line)
                if row:
                    key = _normalize(row["name"])
                    rows[key] = row
    except Exception as e:
        logger.warning(f"Failed to parse manual ELO file {path}: {e}")
        return {}

    return rows


def _ensure_loaded(tour: str) -> dict[str, dict]:
    """Lazy-load and cache the manual ELO data, reloading when the file changes."""
    path = _path(tour)
    try:
        mtime = path.stat().st_mtime if path.exists() else 0.0
    except OSError:
        mtime = 0.0

    with _lock:
        if mtime != _cache_mtime[tour] or not _cache[tour]:
            _cache[tour] = _load_file(tour)
            _cache_mtime[tour] = mtime
            if _cache[tour]:
                logger.info(
                    f"Loaded manual ELO for {tour}: {len(_cache[tour])} players from {path.name}"
                )
        return _cache[tour]


def lookup(name: str, tour: str) -> Optional[dict]:
    """
    Look up manual ELO for a player by name. Returns the row dict or None.

    Strategy:
      1. Exact normalized name match
      2. Last-name exact match (handles "C. Alcaraz" ↔ "Carlos Alcaraz")
      3. Fuzzy match across all loaded names
    """
    rows = _ensure_loaded(tour)
    if not rows:
        return None

    norm = _normalize(name)
    if norm in rows:
        return rows[norm]

    # Last-name exact match
    target_last = _last_name(name)
    if target_last:
        last_matches = [r for k, r in rows.items() if _last_name(r["name"]) == target_last]
        if len(last_matches) == 1:
            return last_matches[0]
        if len(last_matches) > 1:
            # Disambiguate via fuzzy on full name
            names = [r["name"] for r in last_matches]
            picked = match_name(name, names, threshold=0.75)
            if picked:
                for r in last_matches:
                    if r["name"] == picked:
                        return r
            return last_matches[0]

    # Full fuzzy fallback
    all_names = [r["name"] for r in rows.values()]
    picked = match_name(name, all_names, threshold=0.80)
    if picked:
        for r in rows.values():
            if r["name"] == picked:
                return r

    return None


def manual_elo_for_surface(name: str, tour: str, surface: str) -> Optional[float]:
    """Convenience: return the surface-specific manual ELO, or overall ELO as fallback."""
    row = lookup(name, tour)
    if row is None:
        return None
    surface_map = {"hard": "elo_hard", "clay": "elo_clay", "grass": "elo_grass"}
    key = surface_map.get(surface)
    if key and row.get(key):
        return float(row[key])
    return float(row["elo"]) if row.get("elo") else None


def stats() -> dict:
    """Diagnostic info for /elotest or similar commands."""
    atp = _ensure_loaded("ATP")
    wta = _ensure_loaded("WTA")
    return {
        "atp_count": len(atp),
        "wta_count": len(wta),
        "atp_path": str(ATP_PATH),
        "wta_path": str(WTA_PATH),
        "atp_exists": ATP_PATH.exists(),
        "wta_exists": WTA_PATH.exists(),
    }
