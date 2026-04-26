from sqlalchemy import Column, Integer, String, Float, Date, TIMESTAMP, Boolean
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database import Base


class Player(Base):
    __tablename__ = "players"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), nullable=False, unique=True, index=True)
    # Normalized last name stored for fast fuzzy-match queries
    name_last = Column(String(60), nullable=True, index=True)
    tour = Column(String(3), nullable=False)          # ATP / WTA
    current_elo = Column(Float, nullable=True)
    peak_elo = Column(Float, nullable=True)
    elo_hard = Column(Float, nullable=True)
    elo_clay = Column(Float, nullable=True)
    elo_grass = Column(Float, nullable=True)
    ranking = Column(Integer, nullable=True)
    elo_updated_at = Column(Date, nullable=True)
    active = Column(Boolean, default=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    matches_p1 = relationship("Match", foreign_keys="Match.player1_id", back_populates="player1")
    matches_p2 = relationship("Match", foreign_keys="Match.player2_id", back_populates="player2")

    def surface_elo(self, surface: str) -> float:
        """Return surface-specific ELO or fall back to overall ELO."""
        mapping = {"hard": self.elo_hard, "clay": self.elo_clay, "grass": self.elo_grass}
        return mapping.get(surface) or self.current_elo or 1500.0

    def set_name(self, full_name: str) -> None:
        """Set name and extract/store normalized last name for fast search."""
        from app.utils.name_matcher import _last_name
        self.name = full_name
        self.name_last = _last_name(full_name)
