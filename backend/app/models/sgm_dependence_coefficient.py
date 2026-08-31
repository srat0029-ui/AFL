"""Persisted output of scripts/sgm_joint_model_backtest.py's dependence
fit — the "never refit per request" boundary for Same Game Multi joint
pricing (see app/pricing/same_game_pricing.py), same convention as every
other model in this codebase: fitting is a deliberate, versioned, offline
step; pricing only ever reads.

One current row per market ("disposals" | "goals"), upserted wholesale each
time the backtest/fit script runs — same recompute-and-replace convention
as PlayerModelRun/GoalModelRun's own upsert-by-model_name, not append-only
(there's no separate "champion vs candidate" comparison here yet, just one
coefficient per market. A real promotion-gate comparison across rows would
belong in ModelPromotionEvent, which fit-and-persist also logs to
(market="same_game_multi") for the Model Registry to show this evidence
alongside every other promotion decision.)
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class SgmDependenceCoefficient(TimestampMixin, Base):
    __tablename__ = "sgm_dependence_coefficients"
    __table_args__ = (UniqueConstraint("market", name="uq_sgm_dependence_coefficient_market"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    market: Mapped[str] = mapped_column(String(16), nullable=False, index=True)  # "disposals" | "goals"
    slope: Mapped[float] = mapped_column(Float, nullable=False)
    intercept: Mapped[float] = mapped_column(Float, nullable=False)
    n_observations: Mapped[int] = mapped_column(Integer, nullable=False)
    fit_cutoff_year: Mapped[int] = mapped_column(Integer, nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    fitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return f"<SgmDependenceCoefficient {self.market} slope={self.slope:+.4f}>"
