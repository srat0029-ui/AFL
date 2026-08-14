"""OddsProvider: contract for anything that can supply bookmaker odds.

Stage 1 implements this with a manual-entry provider backed by our own
database; a live provider (e.g. The Odds API) is added in a later stage
without changing any code that consumes OddsProvider.
"""

from abc import ABC, abstractmethod

from app.providers.types import OddsQuote


class OddsProvider(ABC):
    """Supplies bookmaker price quotes for a match's markets."""

    @abstractmethod
    def get_odds(self, sport_code: str, match_external_id: str) -> list[OddsQuote]:
        """Return all known price quotes (any bookmaker, any market) for a match."""
        raise NotImplementedError
