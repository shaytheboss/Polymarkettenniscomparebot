from sqlalchemy import Column, Integer, String, TIMESTAMP, Boolean, ForeignKey, BigInteger
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database import Base


class TelegramUser(Base):
    __tablename__ = "telegram_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String(80), nullable=True)
    min_edge_pp = Column(Integer, default=5)           # minimum edge % to receive alerts
    min_model_agreement = Column(Integer, default=15)  # max allowed |table-markov| in pp
    active = Column(Boolean, default=True)
    tours_watched = Column(ARRAY(String), nullable=True)   # ["ATP", "WTA"] or null = all
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    alerts = relationship("Alert", back_populates="user")


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    opportunity_id = Column(Integer, ForeignKey("opportunities.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("telegram_users.id"), nullable=True)
    sent_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    message_text = Column(String(2000), nullable=True)
    telegram_message_id = Column(BigInteger, nullable=True)
    alert_type = Column(String(30), default="OPPORTUNITY")  # OPPORTUNITY / RESOLVED / STATUS

    opportunity = relationship("Opportunity", back_populates="alerts")
    user = relationship("TelegramUser", back_populates="alerts")


class BotSettings(Base):
    """Global bot configuration stored in DB."""
    __tablename__ = "bot_settings"

    key = Column(String(64), primary_key=True)
    value = Column(String(512), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now())
