"""StatsProvider: contract for anything that can supply team/player match statistics.

Concrete implementations (e.g. an AFL Tables / Footywire-derived source) are
added in a later stage.
"""

from abc import ABC, abstractmethod

from app.providers.types import PlayerStatLine, TeamStatLine


class StatsProvider(ABC):
    """Supplies team- and player-level statistics for completed matches."""

    @abstractmethod
    def get_team_match_stats(self, sport_code: str, match_external_id: str) -> list[TeamStatLine]:
        """Return one TeamStatLine per team that played in the given match."""
        raise NotImplementedError

    @abstractmethod
    def get_player_match_stats(self, sport_code: str, match_external_id: str) -> list[PlayerStatLine]:
        """Return one PlayerStatLine per player who appeared in the given match."""
        raise NotImplementedError
