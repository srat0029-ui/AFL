"""One frozen historical comparison between a real bookmaker quote and what
our model believed at that exact moment (Stage: Live Prop Market Logging +
Settlement Tracking, Sections 1-3). The foundation for eventually asking
"were our model-market differences actually useful" — nothing here answers
that yet; this table just makes sure the raw evidence is captured honestly
and cannot be silently rewritten by later work.

Why this needs its own table rather than just joining PlayerPropMarket to
PlayerDisposalProjection/PlayerGoalProjection at query time: those
projection tables are NOT append-only — `python -m app.player_modelling.cli
refresh-live` upserts them IN PLACE (see app/models/player_projection.py's
UniqueConstraint on match_id+player_id) every time new data or lineup
information arrives. A live join would silently show TODAY's model belief
against a quote captured DAYS ago, which is exactly the "recompute
historical model probabilities using newer data" this stage's brief
explicitly forbids. Every model-derived field below is therefore a frozen
COPY taken at the moment this observation was created, never updated
in place afterward (only the settlement fields at the bottom are ever
written to, once, after the match completes).

Only ever created for automated-provider quotes (`PlayerPropMarket.source
!= "manual"`) — see app/player_modelling/prop_observation.py. A quote a
human typed in for testing/inspection isn't real market evidence; Section
9's "Real logged market observations" dataset needs to mean that literally.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class PropMarketObservation(TimestampMixin, Base):
    __tablename__ = "prop_market_observations"

    id: Mapped[int] = mapped_column(primary_key=True)

    quote_id: Mapped[int] = mapped_column(ForeignKey("player_prop_markets.id"), nullable=False, index=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False, index=True)
    bookmaker_id: Mapped[int] = mapped_column(ForeignKey("bookmakers.id"), nullable=False, index=True)

    # Frozen copy of the quote's own identity, so reporting never needs to
    # join back to PlayerPropMarket just to know what market this was.
    market_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    line_type: Mapped[str] = mapped_column(String(16), nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # the provider, e.g. "the_odds_api"

    # The quote side, frozen (Section 1/2).
    offered_odds: Mapped[float] = mapped_column(Float, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)  # = quote.recorded_at, our fetch time
    bookmaker_last_update: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_implied_probability: Mapped[float] = mapped_column(Float, nullable=False)
    devigged_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    overround_removed: Mapped[bool] = mapped_column(nullable=False, default=False)

    # The model side, frozen (Section 2) - never recomputed later even if
    # the promoted model, its calibration, or this player's data changes.
    model_probability: Mapped[float] = mapped_column(Float, nullable=False)
    model_fair_odds: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_mean: Mapped[float] = mapped_column(Float, nullable=False)
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    data_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confidence_tier: Mapped[str] = mapped_column(String(24), nullable=False, index=True)  # already lineup-downgraded, matching what Prop Insights showed
    selection_status_at_observation: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    is_confirmed_at_observation: Mapped[bool] = mapped_column(nullable=False, default=False)

    # The comparison itself, frozen (Section 3) - same math as
    # prop_math.compare_model_to_market, computed once and stored, not
    # re-derived at report time.
    difference_pp: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    expected_value: Mapped[float] = mapped_column(Float, nullable=False)

    # Settlement (Sections 7-8) - all nullable until the match completes
    # and settlement runs; never touched before then, and the fields above
    # are never touched again after (only these three are written to,
    # exactly once, by settle-props).
    actual_stat_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_result: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)  # "won" | "lost" | "push" | "void" | "unresolved"
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Result verification (live-operations stage, Section 15): set only when
    # settlement encounters an actual value that cannot be trusted at face
    # value (e.g. negative) — flagged for manual review rather than silently
    # settled as though the number were legitimate.
    needs_review: Mapped[bool] = mapped_column(nullable=False, default=False)
    review_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    quote: Mapped["PlayerPropMarket"] = relationship(foreign_keys=[quote_id])
    match: Mapped["Match"] = relationship(foreign_keys=[match_id])
    player: Mapped["Player"] = relationship(foreign_keys=[player_id])
    bookmaker: Mapped["Bookmaker"] = relationship(foreign_keys=[bookmaker_id])

    def __repr__(self) -> str:
        return f"<PropMarketObservation quote={self.quote_id} player={self.player_id} diff={self.difference_pp:+.3f} result={self.market_result}>"
