"""A bet the user actually placed with real money — distinct from every
opportunity/multi the app merely surfaced. Nothing here feeds model
training or ranking; this is a personal record-keeping table only.

Model/market fields are a FROZEN copy taken at the moment the bet was
recorded (same reasoning as PropMarketObservation - see that model's
docstring): if the promoted model or its calibration changes later, this
row must keep showing what was actually believed when the bet was placed,
never a value recomputed after the fact. Only the settlement fields at the
bottom are ever written to again, once, after the match completes.

Covers both player markets (disposals/goals - player_id, threshold,
line_type set) and team markets (h2h/line/total - selection, line_value
set) with one shape, mirroring WeeklyShortlistSnapshotItem's same
player-vs-team split so settlement can reuse that same distinction.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin

STATUS_PENDING = "pending"
STATUS_WON = "won"
STATUS_LOST = "lost"
STATUS_PUSH = "push"
STATUS_VOID = "void"

SOURCE_MODE_HIGH_PROBABILITY = "high_probability"
SOURCE_MODE_BEST_VALUE = "best_value"
SOURCE_MODE_BEST_OPPORTUNITY = "best_opportunity"
SOURCE_MODE_FINAL_SHORTLIST = "final_shortlist"
SOURCE_MODE_MANUAL = "manual"


class PlacedBet(TimestampMixin, Base):
    __tablename__ = "placed_bets"

    id: Mapped[int] = mapped_column(primary_key=True)

    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True, index=True)
    opportunity_type: Mapped[str] = mapped_column(String(16), nullable=False)  # "player" | "team"

    label: Mapped[str] = mapped_column(String(200), nullable=False)  # frozen display label, e.g. "Nick Daicos 25+ Disposals"
    selection: Mapped[str] = mapped_column(String(64), nullable=False)  # team name / "over" / "under"
    market_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    line_type: Mapped[str | None] = mapped_column(String(16), nullable=True)  # player markets only
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)  # player markets only
    line_value: Mapped[float | None] = mapped_column(Float, nullable=True)  # team line/total only

    bookmaker: Mapped[str] = mapped_column(String(64), nullable=False)
    odds_taken: Mapped[float] = mapped_column(Float, nullable=False)
    stake: Mapped[float | None] = mapped_column(Float, nullable=True)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    source_mode: Mapped[str] = mapped_column(String(24), nullable=False, index=True)

    # Frozen model/market snapshot at the moment the bet was placed - never
    # recomputed later, same guarantee as PropMarketObservation.
    model_probability: Mapped[float] = mapped_column(Float, nullable=False)
    model_fair_odds: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_tier: Mapped[str] = mapped_column(String(24), nullable=False)
    lineup_status: Mapped[str | None] = mapped_column(String(24), nullable=True)

    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Settlement - reuses the exact same PlayerMatchStat/Match.home_score
    # settlement logic as prop_market_observations / weekly shortlist items
    # (see app/player_modelling/placed_bets.py's settle_placed_bets). All
    # nullable until the match completes; never touched before then.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=STATUS_PENDING, index=True)
    actual_stat_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    match: Mapped["Match"] = relationship(foreign_keys=[match_id])
    player: Mapped["Player | None"] = relationship(foreign_keys=[player_id])

    def __repr__(self) -> str:
        return f"<PlacedBet {self.label!r} @ {self.odds_taken} status={self.status}>"
