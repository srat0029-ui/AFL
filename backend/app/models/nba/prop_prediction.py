"""The prospective record: one model probability for one side of one prop
line, frozen before tip-off together with the market as it stood at that
moment. This table is the evidence base for the project's central question —
"can the model identify mispriced NBA player props prospectively and
consistently beat the closing market?"

A row has three parts, written at three different times, and the columns
are grouped so it is always obvious which is which:

1. ENTRY — written once at insert, before tip-off, never changed.
   What the model believed (`model_probability`, with the projection and
   information cutoff it came from) and what the market offered right then
   (best eligible price, which bookmaker, the de-vigged consensus).
2. CLOSING — written exactly once, after tip-off.
   What the market looked like at the close. Learned later; never an input
   to the entry belief.
3. SETTLEMENT — written exactly once, after the game is final.
   The actual stat value and the result.

The write-once rules are enforced by app/core/prospective.py's
protect_frozen_record (registered in app/models/nba/__init__.py), not left
to each write path's care.

Entry market fields are nullable: a prediction can be frozen for a line no
eligible bookmaker currently offers, which still counts toward calibration
even though it has no CLV.

Uniqueness is one prediction per (projection, line, selection): re-running
the freeze step against the same projection is a no-op, while a NEW
projection (new information or a new model version) may freeze a new
prediction for the same line. Evaluation therefore has to choose its unit
(e.g. the earliest prediction per game/player/market/line) rather than
count every row — see docs/MULTI_SPORT_ARCHITECTURE.md.

CLV itself is not stored: it is arithmetic over the frozen entry and
closing columns (app/core/clv.py), computed at evaluation time.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin

CLOSING_FIELDS: tuple[str, ...] = (
    "closing_captured_at",
    "closing_main_line",
    "closing_price",
    "closing_quote_id",
    "closing_quote_observed_at",
    "closing_consensus_probability",
    "closing_n_bookmakers",
)

SETTLEMENT_FIELDS: tuple[str, ...] = ("settled_at", "actual_value", "outcome", "settlement_note")


class NbaPropPrediction(TimestampMixin, Base):
    __tablename__ = "nba_prop_predictions"
    __table_args__ = (UniqueConstraint("projection_id", "line", "selection", name="uq_nba_prop_prediction_projection_line_selection"),)

    id: Mapped[int] = mapped_column(primary_key=True)

    # --- ENTRY: frozen at insert ------------------------------------------
    projection_id: Mapped[int] = mapped_column(ForeignKey("nba_prop_projections.id"), nullable=False, index=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("nba_games.id"), nullable=False, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("nba_players.id"), nullable=False, index=True)
    market: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    line: Mapped[float] = mapped_column(Float, nullable=False)
    selection: Mapped[str] = mapped_column(String(8), nullable=False)  # "over" | "under"

    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    information_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    model_probability: Mapped[float] = mapped_column(Float, nullable=False)
    model_fair_odds: Mapped[float] = mapped_column(Float, nullable=False)

    predicted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    # The tip-off time as scheduled when the prediction was frozen — a frozen
    # copy, so a later reschedule cannot make a pre-game prediction look late.
    tipoff_at_prediction: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    entry_bookmaker_id: Mapped[int | None] = mapped_column(ForeignKey("bookmakers.id"), nullable=True, index=True)
    entry_quote_id: Mapped[int | None] = mapped_column(ForeignKey("nba_prop_quotes.id"), nullable=True)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)  # best eligible price for this side
    entry_quote_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    entry_consensus_probability: Mapped[float | None] = mapped_column(Float, nullable=True)  # de-vigged, same line
    entry_n_bookmakers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entry_expected_value: Mapped[float | None] = mapped_column(Float, nullable=True)  # model EV per $1 at entry_price

    # --- CLOSING: written once, after tip-off -----------------------------
    closing_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closing_main_line: Mapped[float | None] = mapped_column(Float, nullable=True)  # entry bookmaker's main line at the close
    closing_price: Mapped[float | None] = mapped_column(Float, nullable=True)  # entry bookmaker, SAME line and side
    closing_quote_id: Mapped[int | None] = mapped_column(ForeignKey("nba_prop_quotes.id"), nullable=True)
    closing_quote_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closing_consensus_probability: Mapped[float | None] = mapped_column(Float, nullable=True)  # de-vigged, same line
    closing_n_bookmakers: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- SETTLEMENT: written once, after the game is final ----------------
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)  # won | lost | push | void | unresolved
    settlement_note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    projection: Mapped["NbaPropProjection"] = relationship(foreign_keys=[projection_id])
    game: Mapped["NbaGame"] = relationship(foreign_keys=[game_id])
    player: Mapped["NbaPlayer"] = relationship(foreign_keys=[player_id])
    entry_bookmaker: Mapped["Bookmaker | None"] = relationship(foreign_keys=[entry_bookmaker_id])

    def __repr__(self) -> str:
        return f"<NbaPropPrediction player={self.player_id} {self.market} {self.selection} {self.line} p={self.model_probability:.3f} outcome={self.outcome}>"
