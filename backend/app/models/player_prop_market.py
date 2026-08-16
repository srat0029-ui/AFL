"""Manually-entered bookmaker player-prop offers — the player-market
equivalent of app/models/odds_quote.py. Deliberately append-only, no unique
constraint (mirrors OddsQuote): a bookmaker's price can move, and each entry
is a dated snapshot, not an in-place-updated "current price." No automated
bookmaker integration reads or writes this table — every row here was typed
in by hand (see app/api/routes/player_props.py).
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String
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

    price_decimal: Mapped[float] = mapped_column(Float, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")

    match: Mapped["Match"] = relationship(foreign_keys=[match_id])
    player: Mapped["Player"] = relationship(foreign_keys=[player_id])
    bookmaker: Mapped["Bookmaker"] = relationship(foreign_keys=[bookmaker_id])

    def __repr__(self) -> str:
        return f"<PlayerPropMarket player={self.player_id} {self.market_type}:{self.line_type}:{self.threshold} @ {self.price_decimal}>"
