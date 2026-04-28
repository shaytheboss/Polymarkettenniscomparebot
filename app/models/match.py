from sqlalchemy import Column, Integer, String, Float, TIMESTAMP, Boolean, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database import Base


class Match(Base):
    __tablename__ = "matches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    external_id = Column(String(64), unique=True, nullable=False, index=True)

    player1_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    player2_id = Column(Integer, ForeignKey("players.id"), nullable=False)

    tour = Column(String(3), nullable=False)           # ATP / WTA
    surface = Column(String(10), nullable=False)       # hard / clay / grass
    tournament = Column(String(120), nullable=True)
    round = Column(String(32), nullable=True)
    scheduled_at = Column(TIMESTAMP(timezone=True), nullable=True)

    # Live state (updated on each poll)
    status = Column(String(20), default="scheduled")   # scheduled / live / finished
    p1_sets = Column(Integer, default=0)
    p2_sets = Column(Integer, default=0)
    p1_games = Column(Integer, default=0)              # games in current set
    p2_games = Column(Integer, default=0)
    p1_pts = Column(Integer, default=0)                # points in current game
    p2_pts = Column(Integer, default=0)
    server = Column(Integer, default=0)                # 0=p1, 1=p2
    in_tiebreak = Column(Boolean, default=False)
    score_text = Column(String(80), nullable=True)     # human-readable full score

    # ELO at match time (snapshot)
    p1_elo_at_match = Column(Float, nullable=True)
    p2_elo_at_match = Column(Float, nullable=True)

    # Polymarket
    polymarket_condition_id = Column(String(80), nullable=True, index=True)
    polymarket_slug = Column(String(200), nullable=True)
    last_poly_price_p1 = Column(Float, nullable=True)  # last known P(p1 wins) from PM
    poly_updated_at = Column(TIMESTAMP(timezone=True), nullable=True)

    # Result
    winner_id = Column(Integer, ForeignKey("players.id"), nullable=True)
    final_score = Column(String(80), nullable=True)
    finished_at = Column(TIMESTAMP(timezone=True), nullable=True)

    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now())

    player1 = relationship("Player", foreign_keys=[player1_id], back_populates="matches_p1")
    player2 = relationship("Player", foreign_keys=[player2_id], back_populates="matches_p2")
    opportunities = relationship("Opportunity", back_populates="match")
    snapshots = relationship("MatchSnapshot", back_populates="match", order_by="MatchSnapshot.created_at")


class MatchSnapshot(Base):
    """Point-in-time snapshot of match state + probability calculations."""
    __tablename__ = "match_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False, index=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    # Score at snapshot time
    p1_sets = Column(Integer)
    p2_sets = Column(Integer)
    p1_games = Column(Integer)
    p2_games = Column(Integer)
    p1_pts = Column(Integer)
    p2_pts = Column(Integer)
    server = Column(Integer)
    in_tiebreak = Column(Boolean, default=False)

    # Model outputs
    table_prob_p1 = Column(Float)
    markov_prob_p1 = Column(Float)
    consensus_prob_p1 = Column(Float)
    model_agreement = Column(Float)

    # Markov calibration — serve probabilities used
    p1_serve_prob = Column(Float, nullable=True)
    p2_serve_prob = Column(Float, nullable=True)
    elo_gap = Column(Float, nullable=True)

    # Market
    poly_price_p1 = Column(Float, nullable=True)
    edge_consensus = Column(Float, nullable=True)

    raw_data = Column(JSONB, nullable=True)

    match = relationship("Match", back_populates="snapshots")
