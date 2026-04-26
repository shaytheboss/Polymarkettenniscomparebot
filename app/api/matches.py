from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.database import get_db
from app.models.match import Match, MatchSnapshot

router = APIRouter()


@router.get("/")
async def list_matches(
    status: Optional[str] = Query(None),
    tour: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
):
    q = select(Match).order_by(desc(Match.updated_at)).limit(limit)
    if status:
        q = q.where(Match.status == status)
    if tour:
        q = q.where(Match.tour == tour)
    result = await db.execute(q)
    matches = result.scalars().all()
    return [_match_to_dict(m) for m in matches]


@router.get("/{match_id}")
async def get_match(match_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Match).where(Match.id == match_id))
    match = result.scalar_one_or_none()
    if not match:
        from fastapi import HTTPException
        raise HTTPException(404, "Match not found")
    return _match_to_dict(match, with_snapshots=True)


@router.get("/{match_id}/snapshots")
async def get_snapshots(match_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(MatchSnapshot)
        .where(MatchSnapshot.match_id == match_id)
        .order_by(MatchSnapshot.created_at)
    )
    snaps = result.scalars().all()
    return [_snap_to_dict(s) for s in snaps]


def _match_to_dict(m: Match, with_snapshots: bool = False) -> dict:
    d = {
        "id": m.id,
        "external_id": m.external_id,
        "player1": m.player1.name if m.player1 else None,
        "player2": m.player2.name if m.player2 else None,
        "player1_elo": m.p1_elo_at_match,
        "player2_elo": m.p2_elo_at_match,
        "tour": m.tour,
        "surface": m.surface,
        "tournament": m.tournament,
        "round": m.round,
        "status": m.status,
        "score": {
            "p1_sets": m.p1_sets, "p2_sets": m.p2_sets,
            "p1_games": m.p1_games, "p2_games": m.p2_games,
            "p1_pts": m.p1_pts, "p2_pts": m.p2_pts,
            "server": m.server, "in_tiebreak": m.in_tiebreak,
            "text": m.score_text,
        },
        "poly_price_p1": m.last_poly_price_p1,
        "poly_condition_id": m.polymarket_condition_id,
        "poly_updated_at": m.poly_updated_at.isoformat() if m.poly_updated_at else None,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
    }
    if with_snapshots and m.snapshots:
        last = m.snapshots[-1]
        d["latest_probs"] = _snap_to_dict(last)
    return d


def _snap_to_dict(s: MatchSnapshot) -> dict:
    return {
        "id": s.id,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "table_prob_p1": s.table_prob_p1,
        "markov_prob_p1": s.markov_prob_p1,
        "consensus_prob_p1": s.consensus_prob_p1,
        "model_agreement": s.model_agreement,
        "poly_price_p1": s.poly_price_p1,
        "edge_consensus": s.edge_consensus,
        "score": {
            "p1_sets": s.p1_sets, "p2_sets": s.p2_sets,
            "p1_games": s.p1_games, "p2_games": s.p2_games,
        },
        "notes": s.raw_data.get("table_notes", "") if s.raw_data else "",
        "elo_band": s.raw_data.get("elo_band", "?") if s.raw_data else "?",
    }
