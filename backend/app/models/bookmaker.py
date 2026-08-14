"""Bookmakers are shared across matches and sports — just a name, get-or-created
by the odds API as quotes are entered (see app/api/routes/odds.py)."""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class Bookmaker(TimestampMixin, Base):
    __tablename__ = "bookmakers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)

    odds_quotes: Mapped[list["OddsQuote"]] = relationship(back_populates="bookmaker")

    def __repr__(self) -> str:
        return f"<Bookmaker {self.name}>"
