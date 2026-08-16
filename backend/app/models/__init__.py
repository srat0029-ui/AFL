"""Import every model so Base.metadata is fully populated for Alembic
autogenerate and for create_all() in tests.
"""

from app.models.bookmaker import Bookmaker
from app.models.elo_rating import EloRating
from app.models.expected_lineup import ExpectedLineup, ExpectedLineupStatus
from app.models.goal_model_run import GoalModelRun, GoalModelValidationMetric, PlayerGoalPrediction
from app.models.match import Match, MatchStatus
from app.models.model_run import ModelRun, ModelRunHistory, ModelValidationMetric
from app.models.odds_quote import OddsQuote
from app.models.player import Player
from app.models.player_match_stat import PlayerMatchStat
from app.models.player_model_run import PlayerDisposalPrediction, PlayerModelRun, PlayerModelValidationMetric
from app.models.player_projection import PlayerDisposalProjection, PlayerGoalProjection
from app.models.player_prop_market import PlayerPropMarket
from app.models.poisson_prediction import PoissonMatchPrediction
from app.models.round import Round
from app.models.season import Season
from app.models.sport import Sport
from app.models.team import Team
from app.models.team_match_stat import TeamMatchStat
from app.models.venue import Venue

__all__ = [
    "Sport",
    "Team",
    "Venue",
    "Season",
    "Round",
    "Match",
    "MatchStatus",
    "EloRating",
    "PoissonMatchPrediction",
    "Bookmaker",
    "OddsQuote",
    "ModelRun",
    "ModelRunHistory",
    "ModelValidationMetric",
    "TeamMatchStat",
    "Player",
    "PlayerMatchStat",
    "PlayerModelRun",
    "PlayerModelValidationMetric",
    "PlayerDisposalPrediction",
    "GoalModelRun",
    "GoalModelValidationMetric",
    "PlayerGoalPrediction",
    "ExpectedLineup",
    "ExpectedLineupStatus",
    "PlayerDisposalProjection",
    "PlayerGoalProjection",
    "PlayerPropMarket",
]
