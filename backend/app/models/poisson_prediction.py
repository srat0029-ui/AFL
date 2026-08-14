"""Persisted output of a completed Poisson walk-forward run.

Unlike EloRating (one row per match *per team*, since Elo ratings carry
forward independently per team), this is one row per match: the Poisson
model's prediction is inherently a joint statement about both teams at
once (their goals/behinds, and the derived win/total/margin figures), not
a standalone per-team number.

Recomputed wholesale each time the modelling CLI runs, same as EloRating —
not versioned across configs.
"""

from sqlalchemy import Float, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class PoissonMatchPrediction(TimestampMixin, Base):
    __tablename__ = "poisson_match_predictions"
    __table_args__ = (UniqueConstraint("match_id", name="uq_poisson_prediction_match"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    home_expected_goals: Mapped[float] = mapped_column(Float, nullable=False)
    home_expected_behinds: Mapped[float] = mapped_column(Float, nullable=False)
    away_expected_goals: Mapped[float] = mapped_column(Float, nullable=False)
    away_expected_behinds: Mapped[float] = mapped_column(Float, nullable=False)

    home_win_probability: Mapped[float] = mapped_column(Float, nullable=False)
    draw_probability: Mapped[float] = mapped_column(Float, nullable=False)
    away_win_probability: Mapped[float] = mapped_column(Float, nullable=False)

    expected_total_points: Mapped[float] = mapped_column(Float, nullable=False)
    expected_margin: Mapped[float] = mapped_column(Float, nullable=False)

    actual_total_points: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_margin: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_home_outcome: Mapped[float] = mapped_column(Float, nullable=False)

    match: Mapped["Match"] = relationship()

    def __repr__(self) -> str:
        return f"<PoissonMatchPrediction match={self.match_id} total={self.expected_total_points:.1f}>"
