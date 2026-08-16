"""Bookmakers are shared across matches and sports — just a name, get-or-created
by the odds API as quotes are entered (see app/api/routes/odds.py). A row
created from manual entry has no provider_key/region (both NULL); a row
seen through an automated provider gets them filled in the first time that
bookmaker is encountered (get-or-create by `name`, matching the existing
pattern - see app/player_modelling/prop_odds_ingestion.py's
_normalize_bookmaker) - a provider is never assumed to be the only source
of truth for a bookmaker that already exists from manual entry."""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class Bookmaker(TimestampMixin, Base):
    __tablename__ = "bookmakers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    # The provider's own bookmaker key (e.g. "sportsbet") and region (e.g.
    # "au"), when known - populated the first time an automated provider
    # returns this bookmaker; NULL for a bookmaker only ever seen manually.
    provider_key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    region: Mapped[str | None] = mapped_column(String(8), nullable=True)

    odds_quotes: Mapped[list["OddsQuote"]] = relationship(back_populates="bookmaker")

    def __repr__(self) -> str:
        return f"<Bookmaker {self.name}>"
