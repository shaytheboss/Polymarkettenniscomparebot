from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from datetime import datetime, timezone, date

from app.database import get_db
from app.models.opportunity import Opportunity
from app.models.match import Match

router = APIRouter()


@router.get("")
async def list_opportunities(
    category: Optional[str] = Query(None),   # STRONG / MODERATE / WEAK
    tour: Optional[str] = Query(None),
    min_edge: Optional[float] = Query(None),
    date_from: Optional[date] = Query(None),
    resolved: Optional[bool] = Query(None),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
):
    q = (
        select(Opportunity)
        .join(Match, Opportunity.match_id == Match.id)
        .order_by(desc(Opportunity.detected_at))
        .limit(limit)
    )
    if category:
        q = q.where(Opportunity.edge_category == category.upper())
    if tour:
        q = q.where(Match.tour == tour.upper())
    if min_edge is not None:
        q = q.where(Opportunity.edge_pp >= min_edge)
    if date_from:
        dt = datetime.combine(date_from, datetime.min.time()).replace(tzinfo=timezone.utc)
        q = q.where(Opportunity.detected_at >= dt)
    if resolved is not None:
        q = q.where(Opportunity.resolved == resolved)

    result = await db.execute(q)
    opps = result.scalars().all()
    return [_opp_to_dict(o) for o in opps]


@router.get("/stats")
async def opportunity_stats(db: AsyncSession = Depends(get_db)):
    total_n   = (await db.execute(select(func.count()).select_from(Opportunity))).scalar() or 0
    resolved_n = (await db.execute(
        select(func.count()).select_from(Opportunity).where(Opportunity.resolved == True)
    )).scalar() or 0
    wins_n = (await db.execute(
        select(func.count()).select_from(Opportunity).where(Opportunity.outcome == "WIN")
    )).scalar() or 0
    losses_n = (await db.execute(
        select(func.count()).select_from(Opportunity).where(Opportunity.outcome == "LOSS")
    )).scalar() or 0
    avg_edge_v = (await db.execute(select(func.avg(Opportunity.edge_pp)))).scalar() or 0
    return {
        "total": total_n,
        "resolved": resolved_n,
        "wins": wins_n,
        "losses": losses_n,
        "win_rate": round(wins_n / max(1, wins_n + losses_n), 3),
        "avg_edge_pp": round(float(avg_edge_v), 2),
    }


@router.patch("/{opp_id}/resolve")
async def resolve_opportunity(
    opp_id: int,
    outcome: str,  # WIN / LOSS / VOID
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Opportunity).where(Opportunity.id == opp_id))
    opp = result.scalar_one_or_none()
    if not opp:
        from fastapi import HTTPException
        raise HTTPException(404, "Opportunity not found")
    opp.resolved = True
    opp.outcome = outcome.upper()
    opp.resolved_at = datetime.now(timezone.utc)
    if outcome.upper() == "WIN":
        opp.pnl_units = (1.0 - opp.poly_price) / opp.poly_price
    elif outcome.upper() == "LOSS":
        opp.pnl_units = -1.0
    await db.commit()
    return _opp_to_dict(opp)


def _opp_to_dict(o: Opportunity) -> dict:
    return {
        "id": o.id,
        "match_id": o.match_id,
        "detected_at": o.detected_at.isoformat() if o.detected_at else None,
        "back_player": o.back_player,
        "back_player_name": o.back_player_name,
        "table_prob": round(o.table_prob, 4),
        "markov_prob": round(o.markov_prob, 4),
        "consensus_prob": round(o.consensus_prob, 4),
        "poly_price": round(o.poly_price, 4),
        "edge_pp": round(o.edge_pp, 2),
        "model_agreement": round(o.model_agreement, 2),
        "edge_category": o.edge_category,
        "score_text": o.score_text,
        "score": {
            "p1_sets": o.p1_sets, "p2_sets": o.p2_sets,
            "p1_games": o.p1_games, "p2_games": o.p2_games,
        },
        "alert_sent": o.alert_sent,
        "resolved": o.resolved,
        "outcome": o.outcome,
        "pnl_units": o.pnl_units,
        "extra": o.extra,
    }
