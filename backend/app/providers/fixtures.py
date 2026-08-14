"""FixtureProvider: contract for anything that can supply match fixtures/results.

Concrete implementations (e.g. Squiggle for AFL) are added in a later stage.
"""

from abc import ABC, abstractmethod

from app.providers.types import Fixture


class FixtureProvider(ABC):
    """Supplies fixtures and results for a sport/season."""

    @abstractmethod
    def get_fixtures(self, sport_code: str, season_year: int) -> list[Fixture]:
        """Return all known fixtures (past and/or upcoming) for a season.

        Implementations should return results already known (scores, status)
        alongside not-yet-played fixtures in the same call — callers decide
        what to do with each based on `Fixture.status`.
        """
        raise NotImplementedError

    @abstractmethod
    def get_upcoming_fixtures(self, sport_code: str) -> list[Fixture]:
        """Return fixtures that have not yet been played, across any season."""
        raise NotImplementedError
