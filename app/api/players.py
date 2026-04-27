from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.database import get_db
from app.models.player import Player

router = APIRouter()


@router.get("")
async def list_players(
    tour: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    db: AsyncSession = Depends(get_db),
):
    q = select(Player).order_by(desc(Player.current_elo)).limit(limit)
    if tour:
        q = q.where(Player.tour == tour.upper())
    if search:
        q = q.where(Player.name.ilike(f"%{search}%"))
    result = await db.execute(q)
    return [_player_dict(p) for p in result.scalars().all()]


@router.get("/{player_id}")
async def get_player(player_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Player).where(Player.id == player_id))
    p = result.scalar_one_or_none()
    if not p:
        from fastapi import HTTPException
        raise HTTPException(404, "Player not found")
    return _player_dict(p)


def _player_dict(p: Player) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "tour": p.tour,
        "current_elo": p.current_elo,
        "peak_elo": p.peak_elo,
        "elo_hard": p.elo_hard,
        "elo_clay": p.elo_clay,
        "elo_grass": p.elo_grass,
        "ranking": p.ranking,
        "elo_updated_at": p.elo_updated_at.isoformat() if p.elo_updated_at else None,
        "active": p.active,
    }
