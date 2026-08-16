"""Interpretable confidence tiers for a goal projection — Section 21.
Same rule-based, no-fake-percentage philosophy as
disposal_confidence.py, adapted for goals' extra rare-event dimension:
confidence should also reflect whether this player's scoring rate puts
them in a region (very low or very high) where sample sizes for higher
thresholds get thin.
"""

from dataclasses import dataclass
from enum import Enum

MIN_GAMES_INSUFFICIENT = 5
HIGHER_MIN_GAMES = 30
LOWER_TOG_PERCENTILE_CUTOFF = 25
HIGH_SCORING_RATE_VARIANCE_THRESHOLD = 0.7  # roughly the top quartile of last-5 goals std among real scorers


class GoalConfidenceTier(str, Enum):
    INSUFFICIENT_HISTORY = "insufficient_history"
    LOWER = "lower_confidence"
    MODERATE = "moderate_confidence"
    HIGHER = "higher_confidence"


@dataclass(frozen=True)
class GoalConfidenceInputs:
    games_of_history: int
    tog_last5_avg: float | None
    goals_last5_std: float | None
    league_low_tog_cutoff: float


def classify_goal_confidence(inputs: GoalConfidenceInputs) -> GoalConfidenceTier:
    if inputs.games_of_history < MIN_GAMES_INSUFFICIENT:
        return GoalConfidenceTier.INSUFFICIENT_HISTORY

    unstable_tog = inputs.tog_last5_avg is not None and inputs.tog_last5_avg <= inputs.league_low_tog_cutoff
    high_variance = inputs.goals_last5_std is not None and inputs.goals_last5_std >= HIGH_SCORING_RATE_VARIANCE_THRESHOLD

    if unstable_tog or high_variance:
        return GoalConfidenceTier.LOWER

    if inputs.games_of_history >= HIGHER_MIN_GAMES and not unstable_tog and not high_variance:
        return GoalConfidenceTier.HIGHER

    return GoalConfidenceTier.MODERATE
