"""Bookmakers are shared across matches and sports — just a name, get-or-created
by the odds API as quotes are entered (see app/api/routes/odds.py). A row
created from manual entry has no provider_key/region (both NULL); a row
seen through an automated provider gets them filled in the first time that
bookmaker is encountered (get-or-create by `name`, matching the existing
pattern - see app/player_modelling/prop_odds_ingestion.py's
_normalize_bookmaker) - a provider is never assumed to be the only source
of truth for a bookmaker that already exists from manual entry.

is_exchange/eligibility (Market Integrity + Final Weekly Picks stage,
Sections 4-5, 13): a betting EXCHANGE (e.g. Betfair) publishes a "back"
price set by other bettors, not a bookmaker's risk book - a fundamentally
different price product from a fixed-odds sportsbook that can legitimately
diverge far more sharply, especially for illiquid longshots. Both fields
are set automatically at get-or-create time from the provider's own key
(see app/player_modelling/bookmaker_classification.py -
classify_provider_key; The Odds API marks Betfair's exchange product as
"betfair_ex_au", distinct from any plain sportsbook key) and remain
user-editable afterward via PATCH /api/bookmakers/{id} - never hardcoded
preferences, always visible and overridable."""

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin

ELIGIBILITY_INCLUDED = "included"
ELIGIBILITY_EXCLUDED = "excluded"
ELIGIBILITY_INFORMATIONAL = "informational_only"


class Bookmaker(TimestampMixin, Base):
    __tablename__ = "bookmakers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    # The provider's own bookmaker key (e.g. "sportsbet") and region (e.g.
    # "au"), when known - populated the first time an automated provider
    # returns this bookmaker; NULL for a bookmaker only ever seen manually.
    provider_key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    region: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # True for a betting exchange rather than a fixed-odds sportsbook.
    is_exchange: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # "included" | "excluded" | "informational_only" - defaults to
    # "informational_only" for exchanges (visible everywhere, but never
    # silently used as "the" best price against sportsbook prices) and
    # "included" for everything else. See bookmaker_classification.py.
    eligibility: Mapped[str] = mapped_column(String(24), nullable=False, default=ELIGIBILITY_INCLUDED)

    odds_quotes: Mapped[list["OddsQuote"]] = relationship(back_populates="bookmaker")

    def __repr__(self) -> str:
        return f"<Bookmaker {self.name}>"
