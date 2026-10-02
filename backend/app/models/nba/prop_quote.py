"""One bookmaker's price for one side of one player-prop line, as observed
at one moment. Append-only, no unique constraint (same choice as AFL's
OddsQuote/PlayerPropMarket): a price moves, and every observation is a dated
snapshot, never an in-place "current price". Ingestion writes a new row only
when the price actually changed.

This table IS the market-movement history and the source of the closing
line: the closing line for a bookmaker is simply its last quote with
`observed_at` at or before tip-off.

Both sides of a line ("over" and "under") are stored as separate rows from
the same bookmaker snapshot — that pairing is what makes de-vigging
possible.

`is_alternate_line` separates a bookmaker's MAIN line from its ladder of
alternate lines (the provider reports them under different market keys).
"Did the line move" is only meaningful for the main line.
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class NbaPropQuote(TimestampMixin, Base):
    __tablename__ = "nba_prop_quotes"

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("nba_games.id"), nullable=False, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("nba_players.id"), nullable=False, index=True)
    bookmaker_id: Mapped[int] = mapped_column(ForeignKey("bookmakers.id"), nullable=False, index=True)

    market: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # NbaPropMarket value
    line: Mapped[float] = mapped_column(Float, nullable=False)
    selection: Mapped[str] = mapped_column(String(8), nullable=False)  # "over" | "under"
    is_alternate_line: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    price_decimal: Mapped[float] = mapped_column(Float, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)  # our fetch time
    bookmaker_last_update: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    source: Mapped[str] = mapped_column(String(32), nullable=False)  # the provider, e.g. "the_odds_api"
    provider_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    provider_market_key: Mapped[str | None] = mapped_column(String(48), nullable=True)
    raw_outcome: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    game: Mapped["NbaGame"] = relationship(foreign_keys=[game_id])
    player: Mapped["NbaPlayer"] = relationship(foreign_keys=[player_id])
    bookmaker: Mapped["Bookmaker"] = relationship(foreign_keys=[bookmaker_id])

    def __repr__(self) -> str:
        return f"<NbaPropQuote player={self.player_id} {self.market} {self.selection} {self.line} @ {self.price_decimal}>"
