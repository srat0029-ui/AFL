"""A single bookmaker's price for one market selection at a point in time.

market_type is deliberately a plain string, not a native DB enum: the
architecture anticipates more markets over time (line, total, margin, player
props, ...) and a new one should never require a migration. Validity for
what market_type/selection/line_value combinations make sense is enforced
at the API layer (see app/api/routes/odds.py), not the DB schema.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class OddsQuote(TimestampMixin, Base):
    __tablename__ = "odds_quotes"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    bookmaker_id: Mapped[int] = mapped_column(ForeignKey("bookmakers.id"), nullable=False, index=True)

    market_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # "h2h" | "line" | "total"
    selection: Mapped[str] = mapped_column(String(64), nullable=False)  # team name, or "over"/"under"
    line_value: Mapped[float | None] = mapped_column(Float, nullable=True)  # handicap or total line

    price_decimal: Mapped[float] = mapped_column(Float, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    is_closing_line: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    match: Mapped["Match"] = relationship(back_populates="odds_quotes")
    bookmaker: Mapped["Bookmaker"] = relationship(back_populates="odds_quotes")

    def __repr__(self) -> str:
        return f"<OddsQuote {self.market_type}:{self.selection} @ {self.price_decimal}>"
