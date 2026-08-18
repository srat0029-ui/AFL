"""A frozen, point-in-time copy of the Final Weekly Shortlist (Weekly Bet
Review + Decision Support stage, Sections 14-16) — "What did the app
actually show me at the time?" Every field here is a COPY taken at
creation, never updated in place afterward (only the result-tracking
fields at the bottom of WeeklyShortlistSnapshotItem are ever written to,
once, after the match settles) — same append-only, never-overwritten
philosophy as PropMarketObservation (see prop_market_observation.py).
Creating a new snapshot never modifies or deletes an existing one.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class WeeklyShortlistSnapshot(TimestampMixin, Base):
    __tablename__ = "weekly_shortlist_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    round_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    season_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    limit_requested: Mapped[int | None] = mapped_column(Integer, nullable=True)
    include_unconfirmed_players: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    n_items: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)  # optional user-supplied note, e.g. "before lineups"

    items: Mapped[list["WeeklyShortlistSnapshotItem"]] = relationship(back_populates="snapshot", cascade="all, delete-orphan", order_by="WeeklyShortlistSnapshotItem.rank")

    def __repr__(self) -> str:
        return f"<WeeklyShortlistSnapshot id={self.id} round={self.round_number} n_items={self.n_items}>"


class WeeklyShortlistSnapshotItem(TimestampMixin, Base):
    __tablename__ = "weekly_shortlist_snapshot_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("weekly_shortlist_snapshots.id"), nullable=False, index=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)

    opportunity_type: Mapped[str] = mapped_column(String(16), nullable=False)  # "player" | "team"
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    market_type: Mapped[str] = mapped_column(String(32), nullable=False)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True, index=True)
    selection: Mapped[str | None] = mapped_column(String(64), nullable=True)
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    line_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    line_type: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Frozen odds/model snapshot - exactly what the Shortlist showed.
    best_price: Mapped[float] = mapped_column(Float, nullable=False)
    best_bookmaker: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    model_probability: Mapped[float] = mapped_column(Float, nullable=False)
    model_fair_odds: Mapped[float] = mapped_column(Float, nullable=False)
    market_implied_probability: Mapped[float] = mapped_column(Float, nullable=False)
    devigged_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    overround_removed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    difference_pp: Mapped[float] = mapped_column(Float, nullable=False)
    expected_value: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_tier: Mapped[str] = mapped_column(String(24), nullable=False)
    quality_tier: Mapped[str] = mapped_column(String(24), nullable=False)
    market_maturity_tier: Mapped[str | None] = mapped_column(String(24), nullable=True)
    is_confirmed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    n_bookmakers: Mapped[int] = mapped_column(Integer, nullable=False)

    # Reasons/caveats/evidence, frozen as JSON - exactly what was shown,
    # not re-derivable later from mutable live state.
    reasons_json: Mapped[dict] = mapped_column(JSON, nullable=False)

    # Result tracking (Section 16) - all nullable until settlement runs;
    # never touched before then, and every field above is never touched
    # again after (only these three are ever written to, once).
    actual_stat_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    match_result: Mapped[str | None] = mapped_column(String(16), nullable=True)  # "won" | "lost" | "push" | "unresolved"
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    snapshot: Mapped["WeeklyShortlistSnapshot"] = relationship(back_populates="items")
    match: Mapped["Match"] = relationship(foreign_keys=[match_id])
    player: Mapped["Player | None"] = relationship(foreign_keys=[player_id])

    def __repr__(self) -> str:
        return f"<WeeklyShortlistSnapshotItem snapshot={self.snapshot_id} rank={self.rank} label={self.label!r}>"
