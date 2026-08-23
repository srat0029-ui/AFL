"""The prospective evaluation dataset for the pricing engine (B2B Pricing
Engine stage, item 5): one row per (match, market_type, selection/player/
threshold, model_version) pricing computation, frozen at the moment it was
generated for a still-future event.

This is the evidence base for whether the engine's prices actually contain
predictive information — never overwritten, never re-derived with a newer
model version, and never influenced by settlement. Structurally the same
"freeze now, attach outcome later, once" discipline as PropMarketObservation
and WeeklyShortlistSnapshotItem (see those models' docstrings), generalised
to cover EVERY market the pricing engine can price — including one with no
bookmaker quote at all (`market_consensus_probability` etc. are nullable;
pure model pricing must work independently of whether a market exists).

Uniqueness is on (match_id, market_type, selection, threshold, line_value,
model_version) — the same market re-priced by the SAME model version before
kickoff simply doesn't get a second row (idempotent snapshotting, see
app/pricing/snapshot_service.py); a NEW model_version always gets its own
new rows, so the history of what an older model version believed is never
lost or overwritten.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class PricingSnapshot(Base, TimestampMixin):
    __tablename__ = "pricing_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "match_id", "market_type", "selection", "threshold", "line_value", "model_version",
            name="uq_pricing_snapshot_identity",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True, index=True)
    market_family: Mapped[str] = mapped_column(String(16), nullable=False, index=True)  # "team" | "player_disposals" | "player_goals"
    market_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # "h2h" | "line" | "total" | "player_disposals" | "player_goals"
    selection: Mapped[str] = mapped_column(String(64), nullable=False)  # team name / "over" / "under"
    line_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)  # player multi-plus threshold
    line_value: Mapped[float | None] = mapped_column(Float, nullable=True)  # team handicap/total line

    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lineup_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    confidence_tier: Mapped[str] = mapped_column(String(24), nullable=False)
    # Usage-Change Production Integration stage (item 5): frozen at snapshot
    # time alongside everything else here, never backfilled onto existing
    # rows — lets prospective evaluation later be split stable vs changed to
    # verify (or refute) the historical +11% goal-error finding live. Only
    # ever populated for player markets; None for team snapshots.
    usage_regime_at_prediction: Mapped[str | None] = mapped_column(String(24), nullable=True)

    model_probability: Mapped[float] = mapped_column(Float, nullable=False)
    model_fair_odds: Mapped[float] = mapped_column(Float, nullable=False)

    # Market context AT THE SAME MOMENT, frozen alongside the model price -
    # nullable throughout: a market snapshot must be capturable even when no
    # bookmaker currently offers this market at all (item 2's requirement
    # that pricing works independently of market availability).
    best_bookmaker_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_bookmaker_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    market_consensus_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    n_bookmakers: Mapped[int | None] = mapped_column(nullable=True)

    # Settlement - reuses the exact same PlayerMatchStat/Match.home_score
    # settlement primitives as prop_market_observations / placed_bets (see
    # app/pricing/snapshot_service.py's settle_pricing_snapshots). All
    # nullable until the match completes; never touched before then, and
    # every field above is never touched again after that either.
    actual_stat_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)  # "won" | "lost" | "push" | "void"
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    match: Mapped["Match"] = relationship(foreign_keys=[match_id])
    player: Mapped["Player | None"] = relationship(foreign_keys=[player_id])

    def __repr__(self) -> str:
        return f"<PricingSnapshot match={self.match_id} {self.market_type} p={self.model_probability:.3f} v={self.model_version}>"
