from sqlalchemy import Column, Integer, String, Float, TIMESTAMP, Boolean, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database import Base


class Opportunity(Base):
    __tablename__ = "opportunities"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False, index=True)
    detected_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    # Which player to back
    back_player = Column(Integer, nullable=False)      # 1 or 2
    back_player_name = Column(String(120), nullable=False)

    # Probabilities at detection time
    table_prob = Column(Float, nullable=False)
    markov_prob = Column(Float, nullable=False)
    consensus_prob = Column(Float, nullable=False)
    poly_price = Column(Float, nullable=False)
    edge_pp = Column(Float, nullable=False)            # consensus - poly in pp
    model_agreement = Column(Float, nullable=False)    # |table - markov|

    # Match state at detection
    score_text = Column(String(80), nullable=True)
    p1_sets = Column(Integer)
    p2_sets = Column(Integer)
    p1_games = Column(Integer)
    p2_games = Column(Integer)

    # Edge category for filtering
    edge_category = Column(String(20), nullable=True)  # STRONG / MODERATE / WEAK

    # Alert
    alert_sent = Column(Boolean, default=False)
    alert_sent_at = Column(TIMESTAMP(timezone=True), nullable=True)

    # Resolution
    resolved = Column(Boolean, default=False)
    outcome = Column(String(10), nullable=True)        # WIN / LOSS / VOID
    resolved_at = Column(TIMESTAMP(timezone=True), nullable=True)
    pnl_units = Column(Float, nullable=True)           # +/- at implied Polymarket price

    extra = Column(JSONB, nullable=True)

    match = relationship("Match", back_populates="opportunities")
    alerts = relationship("Alert", back_populates="opportunity")
