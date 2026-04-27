from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional

from app.database import get_db
from app.models.alert import BotSettings

router = APIRouter()


class SettingUpdate(BaseModel):
    value: str


@router.get("")
async def get_settings(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(BotSettings))
    settings_list = result.scalars().all()
    return {s.key: s.value for s in settings_list}


@router.put("/{key}")
async def update_setting(key: str, body: SettingUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(BotSettings).where(BotSettings.key == key))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = body.value
    else:
        setting = BotSettings(key=key, value=body.value)
        db.add(setting)
    await db.commit()
    return {"key": key, "value": body.value}
