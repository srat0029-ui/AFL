"""The prospective evaluation dataset for Same Game Multi joint pricing —
the SGM-specific sibling of app/models/pricing_snapshot.py, kept as its own
parent+child table pair rather than bolted onto PricingSnapshot because a
joint SGM price is fundamentally multi-leg (PricingSnapshot is one
selection per row) — the same reason PropMarketObservation exists as its
own table alongside PricingSnapshot rather than merging into it.

Same freeze-once/settle-once/never-overwrite discipline as every other
prospective table in this codebase: `SgmPriceSnapshot` is the joint-price
parent row, `SgmSnapshotLeg` is one child row per leg (enough to
independently re-settle and audit each leg, and to reconstruct the exact
combo later).

Two things genuinely new here, not reused from PricingSnapshot's pattern:

1. `dependence_coefficients_used` denormalizes the raw slope/intercept
   values that were actually live at pricing time. SgmDependenceCoefficient
   (app/models/sgm_dependence_coefficient.py) is upserted IN PLACE — a
   future refit overwrites it with no history — so referencing only
   `model_version` on a snapshot would make an old snapshot's exact
   coefficient irreproducible after that refit. Denormalizing the raw
   values here makes every frozen price fully self-contained.

2. `snapshot_horizon` is part of the uniqueness key, not just
   `model_version`. PricingSnapshot/PropMarketObservation both freeze ONCE
   per model_version and never again until the model changes; SGM
   snapshots are deliberately taken repeatedly across the pre-match window
   (24h+, 6-24h, 1-6h, <1h before kickoff) so the model's own belief can be
   tracked as it evolves, which needs its own idempotency dimension.

`bookmaker_sgm_price`/`bookmaker_sgm_name`/`bookmaker_implied_probability`/
`model_edge` are nullable and, as of this write-up, ALWAYS null: no odds
provider integration in this codebase ingests a genuine bookmaker Same
Game Multi/parlay price. The columns exist so evaluation against a real
bookmaker SGM quote activates automatically if a provider ever adds one,
without a schema change — not because that data exists today.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin

SNAPSHOT_HORIZON_24H_PLUS = "24h_plus"
SNAPSHOT_HORIZON_6H_24H = "6h_24h"
SNAPSHOT_HORIZON_1H_6H = "1h_6h"
SNAPSHOT_HORIZON_UNDER_1H = "under_1h"
SNAPSHOT_HORIZONS = (SNAPSHOT_HORIZON_24H_PLUS, SNAPSHOT_HORIZON_6H_24H, SNAPSHOT_HORIZON_1H_6H, SNAPSHOT_HORIZON_UNDER_1H)


class SgmPriceSnapshot(TimestampMixin, Base):
    __tablename__ = "sgm_price_snapshots"
    __table_args__ = (
        UniqueConstraint("match_id", "leg_signature", "model_version", "snapshot_horizon", name="uq_sgm_price_snapshot_identity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)

    # A canonical, deterministic string encoding every leg (sorted) - the
    # identity of "this exact combo", independent of which Multi Builder
    # tier/mode happened to surface it. e.g.
    # "disposals:1234:21.5|goals:5678:0.5|h2h:home".
    leg_signature: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    n_legs: Mapped[int] = mapped_column(Integer, nullable=False)
    # Denormalized sorted set of leg TYPES only (e.g. "disposals+goals+h2h"),
    # not the full leg_signature (which is specific down to player/threshold)
    # - lets sgm_prospective_evaluation.py's "by leg/market combination"
    # split group cheaply without an N+1 query loading every snapshot's legs.
    leg_type_combination: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    snapshot_horizon: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    hours_to_kickoff: Mapped[float] = mapped_column(Float, nullable=False)

    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    dependence_coefficients_used: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data_cutoff: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    model_probability: Mapped[float] = mapped_column(Float, nullable=False)
    naive_independence_probability: Mapped[float] = mapped_column(Float, nullable=False)
    correlation_adjustment_pp: Mapped[float] = mapped_column(Float, nullable=False)
    model_fair_odds: Mapped[float] = mapped_column(Float, nullable=False)
    naive_independence_fair_odds: Mapped[float] = mapped_column(Float, nullable=False)
    mc_standard_error: Mapped[float] = mapped_column(Float, nullable=False)
    n_simulations: Mapped[int] = mapped_column(Integer, nullable=False)
    dependence_validated: Mapped[bool] = mapped_column(nullable=False, default=False)

    # Always null today - see module docstring. Populated defensively so
    # evaluation activates automatically if a provider ever exposes this.
    bookmaker_sgm_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    bookmaker_sgm_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bookmaker_implied_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_edge: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Settlement - nullable until the match resolves, never touched again
    # after that (same write-once discipline as PricingSnapshot/PropMarketObservation).
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)  # "won" | "lost" | "push" | "void"
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    match: Mapped["Match"] = relationship(foreign_keys=[match_id])
    legs: Mapped[list["SgmSnapshotLeg"]] = relationship(back_populates="snapshot", cascade="all, delete-orphan", order_by="SgmSnapshotLeg.leg_index")

    def __repr__(self) -> str:
        return f"<SgmPriceSnapshot match={self.match_id} legs={self.n_legs} horizon={self.snapshot_horizon} p={self.model_probability:.3f}>"


class SgmSnapshotLeg(TimestampMixin, Base):
    __tablename__ = "sgm_snapshot_legs"
    __table_args__ = (UniqueConstraint("snapshot_id", "leg_index", name="uq_sgm_snapshot_leg_index"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("sgm_price_snapshots.id"), nullable=False, index=True)
    leg_index: Mapped[int] = mapped_column(Integer, nullable=False)

    leg_type: Mapped[str] = mapped_column(String(16), nullable=False)  # "h2h" | "total" | "disposals" | "goals"
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    # Frozen at creation, not re-joined at settlement time: team name for
    # h2h, "over"/"under" for total, "over" for player legs (every
    # observation is the "over" side by the same convention prop_settlement.py
    # already uses) - settling later never depends on today's team/player state.
    selection: Mapped[str] = mapped_column(String(64), nullable=False)
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    line_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    naive_leg_probability: Mapped[float] = mapped_column(Float, nullable=False)

    # Settlement - nullable until resolved, write-once after that.
    actual_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    leg_outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)  # "won" | "lost" | "push" | "void"
    leg_resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    snapshot: Mapped["SgmPriceSnapshot"] = relationship(back_populates="legs")
    team: Mapped["Team | None"] = relationship(foreign_keys=[team_id])
    player: Mapped["Player | None"] = relationship(foreign_keys=[player_id])

    def __repr__(self) -> str:
        return f"<SgmSnapshotLeg snapshot={self.snapshot_id} #{self.leg_index} {self.leg_type} outcome={self.leg_outcome}>"
