"""Interpretable confidence tiers for a disposal projection — Section 20 of
the brief. Deliberately rule-based on real, checkable inputs (not a fitted
"confidence model" producing an arbitrary-looking percentage): the brief
explicitly warns against a fake "87% confidence" score, and the real
analysis in disposal_analysis.py backs each rule used here with an
observed effect on error (see each condition's comment for the specific
finding it's based on).

Four tiers, most to least trustworthy:
  INSUFFICIENT_HISTORY - too little player history to trust a rolling
    average at all (disposal_analysis.by_history_bucket found the <10-game
    bucket has the highest MAE AND a strong positive bias - the model
    over-predicts new/fringe players toward the league average).
  LOWER - enough history to predict, but a real reason to distrust it:
    unstable recent time-on-ground (disposal_analysis.compare_with_
    without_tog found low-recent-TOG rows have meaningfully worse MAE than
    stable-TOG rows) or high recent disposal volatility (a genuinely
    inconsistent player is harder to pin down regardless of how much
    history exists).
  MODERATE - the default when neither a "higher" nor a "lower" condition
    is met.
  HIGHER - an established player (75+ games matches the best-performing
    history bucket) with stable recent TOG and low recent disposal
    variance.
"""

from dataclasses import dataclass
from enum import Enum

MIN_GAMES_INSUFFICIENT = 5
HIGHER_MIN_GAMES = 30  # by_history_bucket: 10-30 and 30-75 buckets both outperform <10 and 75+ on MAE
LOWER_TOG_PERCENTILE_CUTOFF = 25  # bottom quartile of recent TOG - the split disposal_analysis.py's TOG comparison used
HIGH_VARIANCE_STD_THRESHOLD = 6.0  # roughly the top quartile of last-5 disposal std across the real eval set


class ConfidenceTier(str, Enum):
    INSUFFICIENT_HISTORY = "insufficient_history"
    LOWER = "lower_confidence"
    MODERATE = "moderate_confidence"
    HIGHER = "higher_confidence"


@dataclass(frozen=True)
class ConfidenceInputs:
    games_of_history: int
    tog_last5_avg: float | None
    disposals_last5_std: float | None
    league_low_tog_cutoff: float  # the bottom-quartile TOG value from the reference (tune) set - passed in, not guessed per row


def classify_confidence(inputs: ConfidenceInputs) -> ConfidenceTier:
    if inputs.games_of_history < MIN_GAMES_INSUFFICIENT:
        return ConfidenceTier.INSUFFICIENT_HISTORY

    unstable_tog = inputs.tog_last5_avg is not None and inputs.tog_last5_avg <= inputs.league_low_tog_cutoff
    high_variance = inputs.disposals_last5_std is not None and inputs.disposals_last5_std >= HIGH_VARIANCE_STD_THRESHOLD

    if unstable_tog or high_variance:
        return ConfidenceTier.LOWER

    if inputs.games_of_history >= HIGHER_MIN_GAMES and not unstable_tog and not high_variance:
        return ConfidenceTier.HIGHER

    return ConfidenceTier.MODERATE
