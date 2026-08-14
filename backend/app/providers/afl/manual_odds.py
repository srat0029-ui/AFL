"""OddsProvider implementation backed by our own database (manually-entered
odds, per app/api/routes/odds.py).

This exists so Stage 1.5's edge calculation — and anything else that wants
"what odds do we know about for this match" — can call OddsProvider.get_odds
without caring whether the quotes came from manual entry or a future live
provider (e.g. The Odds API in a later stage). "match_external_id" here is
just our own Match.id, stringified: manual entry has no third-party id to
speak of, it's tied directly to a match already in our database.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, Sport
from app.models import OddsQuote as OddsQuoteRow
from app.providers.odds import OddsProvider
from app.providers.types import OddsQuote


class ManualOddsProvider(OddsProvider):
    def __init__(self, db: Session):
        self._db = db

    def get_odds(self, sport_code: str, match_external_id: str) -> list[OddsQuote]:
        match = self._db.get(Match, int(match_external_id))
        if match is None:
            return []
        sport = self._db.get(Sport, match.sport_id)
        if sport is None or sport.code != sport_code:
            return []

        rows = self._db.scalars(select(OddsQuoteRow).where(OddsQuoteRow.match_id == match.id)).all()
        return [self._to_dto(row, sport_code) for row in rows]

    @staticmethod
    def _to_dto(row: OddsQuoteRow, sport_code: str) -> OddsQuote:
        return OddsQuote(
            match_external_id=str(row.match_id),
            sport_code=sport_code,
            bookmaker=row.bookmaker.name,
            market_type=row.market_type,
            selection=row.selection,
            price_decimal=row.price_decimal,
            recorded_at=row.recorded_at,
            line_value=row.line_value,
            is_closing_line=row.is_closing_line,
            source=row.source,
        )
