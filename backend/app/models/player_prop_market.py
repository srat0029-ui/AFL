"""Bookmaker player-prop offers, manually-entered OR automated — the
player-market equivalent of app/models/odds_quote.py. Deliberately
append-only, no unique constraint (mirrors OddsQuote): a bookmaker's price
can move, and each entry is a dated snapshot, not an in-place-updated
"current price." `source` distinguishes provenance ("manual", or a
provider name like "the_odds_api" — see
app/player_modelling/prop_odds_ingestion.py); manual entry still goes
through app/api/routes/player_projections.py's create_player_prop.

Automated-ingestion-only fields (provider_event_id, provider_market_key,
bookmaker_last_update, raw_outcome) are nullable because manual rows never
populate them — there is no provider event, no provider market key, and no
separate "bookmaker's own last-updated time" for a price a human typed in
(recorded_at already covers when the row was captured).

`selection` distinguishes which side of a market this row is: "over" or
"under" for an over_under line, or "yes" for a multi_plus (N+) line. NULL
on older rows predating this column - application code treats NULL as
"over" (the only side the original manual-entry form ever collected), so
existing data doesn't need a backfill to stay meaningful. Storing both
sides of the SAME over/under line as two separate rows (same bookmaker,
same threshold, same snapshot) is what makes devigging possible (Section
15 of the automated-odds stage) — the original single-sided design didn't
need this because manual entry only ever asked for one price.
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class PlayerPropMarket(TimestampMixin, Base):
    __tablename__ = "player_prop_markets"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False, index=True)
    bookmaker_id: Mapped[int] = mapped_column(ForeignKey("bookmakers.id"), nullable=False, index=True)

    market_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # PlayerMarket value, e.g. "player_disposals"
    line_type: Mapped[str] = mapped_column(String(16), nullable=False)  # LineType value: "over_under" | "multi_plus"
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    selection: Mapped[str | None] = mapped_column(String(16), nullable=True)  # "over" | "under" | "yes"; NULL on pre-automation rows means "over"

    price_decimal: Mapped[float] = mapped_column(Float, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")

    # Automated-provider provenance (see class docstring) - always NULL for source="manual".
    provider_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    provider_market_key: Mapped[str | None] = mapped_column(String(48), nullable=True)
    bookmaker_last_update: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_outcome: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    match: Mapped["Match"] = relationship(foreign_keys=[match_id])
    player: Mapped["Player"] = relationship(foreign_keys=[player_id])
    bookmaker: Mapped["Bookmaker"] = relationship(foreign_keys=[bookmaker_id])

    def __repr__(self) -> str:
        return f"<PlayerPropMarket player={self.player_id} {self.market_type}:{self.line_type}:{self.threshold} @ {self.price_decimal}>"
