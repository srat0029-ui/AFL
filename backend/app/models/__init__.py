"""Import every model so Base.metadata is fully populated for Alembic
autogenerate and for create_all() in tests.
"""

from app.models.elo_rating import EloRating
from app.models.match import Match, MatchStatus
from app.models.round import Round
from app.models.season import Season
from app.models.sport import Sport
from app.models.team import Team
from app.models.venue import Venue

__all__ = ["Sport", "Team", "Venue", "Season", "Round", "Match", "MatchStatus", "EloRating"]
