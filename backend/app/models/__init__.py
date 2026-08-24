"""Import every model so Base.metadata is fully populated for Alembic
autogenerate and for create_all() in tests.
"""

from app.models.anomaly_alert_snapshot import AnomalyAlertSnapshot
from app.models.anomaly_case_record import AnomalyCaseRecord
from app.models.bookmaker import Bookmaker
from app.models.elo_rating import EloRating
from app.models.expected_lineup import (
    CONFIRMED_SELECTION_STATUSES,
    ExpectedLineup,
    ExpectedLineupStatus,
    SelectionStatus,
    derive_coarse_status,
)
from app.models.goal_model_run import GoalModelRun, GoalModelValidationMetric, PlayerGoalPrediction
from app.models.live_cycle_run import (
    RUN_BLOCKED,
    RUN_OK,
    RUN_PARTIAL,
    STEP_BLOCKING_FAILURE,
    STEP_RECOVERABLE_FAILURE,
    STEP_SUCCESS,
    STEP_WARNING,
    LiveCycleRun,
)
from app.models.match import Match, MatchStatus
from app.models.match_context import (
    CONTEXT_CONFIDENCE_LABELS,
    CONTEXT_TYPE_LABELS,
    PLAYER_CONTEXT_TYPES,
    ContextConfidence,
    ContextType,
    MatchContextItem,
)
from app.models.model_promotion_event import ModelPromotionEvent
from app.models.model_run import ModelRun, ModelRunHistory, ModelValidationMetric
from app.models.odds_quote import OddsQuote
from app.models.placed_bet import (
    SOURCE_MODE_BEST_OPPORTUNITY,
    SOURCE_MODE_BEST_VALUE,
    SOURCE_MODE_FINAL_SHORTLIST,
    SOURCE_MODE_HIGH_PROBABILITY,
    SOURCE_MODE_MANUAL,
    STATUS_LOST,
    STATUS_PENDING,
    STATUS_PUSH,
    STATUS_VOID,
    STATUS_WON,
    PlacedBet,
)
from app.models.player import Player
from app.models.player_alias import PlayerAlias
from app.models.player_match_stat import PlayerMatchStat
from app.models.player_model_run import PlayerDisposalPrediction, PlayerModelRun, PlayerModelValidationMetric
from app.models.player_projection import PlayerDisposalProjection, PlayerGoalProjection
from app.models.player_prop_market import PlayerPropMarket
from app.models.pricing_snapshot import PricingSnapshot
from app.models.poisson_prediction import PoissonMatchPrediction
from app.models.prop_market_observation import PropMarketObservation
from app.models.round import Round
from app.models.season import Season
from app.models.sport import Sport
from app.models.team import Team
from app.models.team_match_stat import TeamMatchStat
from app.models.venue import Venue
from app.models.venue_weather import VenueWeatherSnapshot
from app.models.weekly_shortlist_snapshot import WeeklyShortlistSnapshot, WeeklyShortlistSnapshotItem

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
    "SelectionStatus",
    "CONFIRMED_SELECTION_STATUSES",
    "derive_coarse_status",
    "PlayerDisposalProjection",
    "PlayerGoalProjection",
    "PlayerPropMarket",
    "PropMarketObservation",
    "PlayerAlias",
    "LiveCycleRun",
    "RUN_OK",
    "RUN_PARTIAL",
    "RUN_BLOCKED",
    "STEP_SUCCESS",
    "STEP_WARNING",
    "STEP_RECOVERABLE_FAILURE",
    "STEP_BLOCKING_FAILURE",
    "WeeklyShortlistSnapshot",
    "WeeklyShortlistSnapshotItem",
    "MatchContextItem",
    "ContextType",
    "ContextConfidence",
    "PLAYER_CONTEXT_TYPES",
    "CONTEXT_TYPE_LABELS",
    "CONTEXT_CONFIDENCE_LABELS",
    "VenueWeatherSnapshot",
    "PlacedBet",
    "STATUS_PENDING",
    "STATUS_WON",
    "STATUS_LOST",
    "STATUS_PUSH",
    "STATUS_VOID",
    "SOURCE_MODE_HIGH_PROBABILITY",
    "SOURCE_MODE_BEST_VALUE",
    "SOURCE_MODE_BEST_OPPORTUNITY",
    "SOURCE_MODE_FINAL_SHORTLIST",
    "SOURCE_MODE_MANUAL",
    "PricingSnapshot",
    "ModelPromotionEvent",
    "AnomalyAlertSnapshot",
    "AnomalyCaseRecord",
]
