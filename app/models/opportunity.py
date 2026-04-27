from sqlalchemy import Column, Integer, String, Float, TIMESTAMP, Boolean, ForeignKey
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
    back_player = Column(Integer, nullable=False)          # 1 or 2
    back_player_name = Column(String(120), nullable=False)

    # Probabilities at detection time
    table_prob = Column(Float, nullable=False)
    markov_prob = Column(Float, nullable=False)
    consensus_prob = Column(Float, nullable=False)
    poly_price = Column(Float, nullable=False)
    edge_pp = Column(Float, nullable=False)                # consensus - poly in pp
    model_agreement = Column(Float, nullable=False)        # |table - markov| in pp

    # ELO context (stored for historical analysis)
    elo_gap = Column(Float, nullable=True)                 # fav_elo - und_elo
    elo_band = Column(String(4), nullable=True)            # E0/E1/E2/E3
    surface = Column(String(10), nullable=True)
    tour = Column(String(3), nullable=True)

    # Edge classification
    edge_category = Column(String(20), nullable=True)      # STRONG / MODERATE / WEAK
    edge_type = Column(String(40), nullable=True)          # SET1_LOSS_HEAVY_FAV / SERVING_FOR_MATCH / ...

    # Markov serve probabilities used
    p1_serve_prob = Column(Float, nullable=True)
    p2_serve_prob = Column(Float, nullable=True)

    # Kelly fraction at detection time
    kelly_fraction = Column(Float, nullable=True)

    # Match state at detection
    score_text = Column(String(80), nullable=True)
    p1_sets = Column(Integer)
    p2_sets = Column(Integer)
    p1_games = Column(Integer)
    p2_games = Column(Integer)

    # Alert
    alert_sent = Column(Boolean, default=False)
    alert_sent_at = Column(TIMESTAMP(timezone=True), nullable=True)

    # Resolution
    resolved = Column(Boolean, default=False)
    outcome = Column(String(10), nullable=True)            # WIN / LOSS / VOID
    resolved_at = Column(TIMESTAMP(timezone=True), nullable=True)
    pnl_units = Column(Float, nullable=True)               # +/- at implied Polymarket price

    extra = Column(JSONB, nullable=True)

    match = relationship("Match", back_populates="opportunities")
    alerts = relationship("Alert", back_populates="opportunity")
