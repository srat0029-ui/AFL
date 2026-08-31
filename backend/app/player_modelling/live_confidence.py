"""Confidence tiers and explicit-limitation warnings for LIVE projections —
Sections 6-7 of the live-projection brief. Reuses the already-validated,
rule-based tier classifiers from disposal_confidence.py/goal_confidence.py
(no new "confidence model," per that module's own no-fake-percentage
philosophy) and adds exactly one new dimension neither research-stage
classifier had a reason to consider: availability/lineup uncertainty, which
only exists for a live, not-yet-played match.

Every warning string here is driven by a real, checkable condition computed
at generation time (games_of_history, TOG stability, lineup status,
scoring archetype, rare-threshold sample size) — never invented free text.
"""

from dataclasses import dataclass

from app.player_modelling.disposal_confidence import (
    ConfidenceInputs,
    ConfidenceTier,
    HIGH_VARIANCE_STD_THRESHOLD,
    MIN_GAMES_INSUFFICIENT,
    classify_confidence,
)
from app.player_modelling.goal_confidence import (
    GoalConfidenceInputs,
    HIGH_SCORING_RATE_VARIANCE_THRESHOLD,
    classify_goal_confidence,
)

LEAGUE_LOW_TOG_CUTOFF = 60.0  # matches disposal_report_query.py / goal_report_query.py's fixed reference value

_TIER_ORDER = [
    ConfidenceTier.INSUFFICIENT_HISTORY.value,
    ConfidenceTier.LOWER.value,
    ConfidenceTier.MODERATE.value,
    ConfidenceTier.HIGHER.value,
]

ARCHETYPE_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("very_low", 0.0, 0.15),
    ("occasional", 0.15, 0.4),
    ("regular", 0.4, 0.8),
    ("high_volume", 0.8, float("inf")),
)


def classify_scoring_archetype(goals_career_avg: float | None) -> str:
    """Same boundaries as goal_analysis.ARCHETYPE_BUCKETS, based purely on
    a point-in-time career scoring RATE — never an invented position label
    (Section 18 of the goal-prediction stage brief)."""
    rate = goals_career_avg or 0.0
    for label, lo, hi in ARCHETYPE_BUCKETS:
        if lo <= rate < hi:
            return label
    return "high_volume"


def _downgrade_one_step(tier: str) -> str:
    idx = _TIER_ORDER.index(tier)
    return _TIER_ORDER[max(idx - 1, 0)]


def _lineup_adjusted(base_tier: str, lineup_status: str) -> str:
    """An UNCERTAIN lineup status downgrades confidence by one step -
    Section 6's "availability uncertainty" input. EXPECTED_IN makes no
    adjustment; EXPECTED_OUT players are never projected at all (see
    upcoming_features.load_expected_players), so it never reaches here."""
    if lineup_status == "uncertain":
        return _downgrade_one_step(base_tier)
    return base_tier


@dataclass(frozen=True)
class LiveDisposalConfidence:
    tier: str
    warnings: list[str]


def live_disposal_confidence(
    games_of_history: int, tog_last5_avg: float | None, disposals_last5_std: float | None, lineup_status: str
) -> LiveDisposalConfidence:
    base = classify_confidence(
        ConfidenceInputs(
            games_of_history=games_of_history,
            tog_last5_avg=tog_last5_avg,
            disposals_last5_std=disposals_last5_std,
            league_low_tog_cutoff=LEAGUE_LOW_TOG_CUTOFF,
        )
    )
    tier = _lineup_adjusted(base.value, lineup_status)

    warnings: list[str] = []
    if games_of_history < MIN_GAMES_INSUFFICIENT:
        warnings.append("Limited AFL history — this player has fewer than 5 tracked games.")
    if tog_last5_avg is not None and tog_last5_avg <= LEAGUE_LOW_TOG_CUTOFF:
        warnings.append("Recent time-on-ground has been low, which the model treats as a confidence signal.")
    if disposals_last5_std is not None and disposals_last5_std >= HIGH_VARIANCE_STD_THRESHOLD:
        warnings.append("Recent disposal counts have been volatile game-to-game.")
    if lineup_status == "uncertain":
        warnings.append("Expected lineup not confirmed — this player is marked uncertain to play.")

    return LiveDisposalConfidence(tier=tier, warnings=warnings)


@dataclass(frozen=True)
class LiveGoalConfidence:
    tier: str
    warnings: list[str]
    scoring_archetype: str


def live_goal_confidence(
    games_of_history: int,
    tog_last5_avg: float | None,
    goals_last5_std: float | None,
    goals_career_avg: float | None,
    lineup_status: str,
) -> LiveGoalConfidence:
    base = classify_goal_confidence(
        GoalConfidenceInputs(
            games_of_history=games_of_history,
            tog_last5_avg=tog_last5_avg,
            goals_last5_std=goals_last5_std,
            league_low_tog_cutoff=LEAGUE_LOW_TOG_CUTOFF,
        )
    )
    tier = _lineup_adjusted(base.value, lineup_status)
    archetype = classify_scoring_archetype(goals_career_avg)

    warnings: list[str] = []
    if games_of_history < MIN_GAMES_INSUFFICIENT:
        warnings.append("Limited AFL history — this player has fewer than 5 tracked games.")
    if tog_last5_avg is not None and tog_last5_avg <= LEAGUE_LOW_TOG_CUTOFF:
        warnings.append("Recent time-on-ground has been low, which the model treats as a confidence signal.")
    if goals_last5_std is not None and goals_last5_std >= HIGH_SCORING_RATE_VARIANCE_THRESHOLD:
        warnings.append("Recent goal-scoring has been volatile game-to-game.")
    if archetype == "high_volume":
        warnings.append("High-volume goal scorers have historically been slightly under-predicted by this model.")
    if lineup_status == "uncertain":
        warnings.append("Expected lineup not confirmed — this player is marked uncertain to play.")

    return LiveGoalConfidence(tier=tier, warnings=warnings, scoring_archetype=archetype)


# Thresholds beyond which the model's own historical validation sample gets
# thin (see disposal_cli.py's/goal_cli.py's real threshold-calibration
# output: goal 3+/4+/5+ and disposal 35+ all carry low_sample_warning=True
# on the actual persisted research runs) — used to attach a rare-event
# caveat to an arbitrary-line query landing at or above these thresholds,
# rather than only to the fixed list of pre-defined thresholds.
DISPOSAL_RARE_THRESHOLD = 35.0
GOAL_RARE_THRESHOLD = 3.0


def rare_event_warning(market: str, threshold: float, model_probability: float) -> str | None:
    """Section 13/18: "be especially careful... do not claim excellent
    calibration from tiny bins." Flags a query as rare-event either because
    it's past the threshold where the research-stage calibration tables
    themselves reported a low-sample warning, or because the model's own
    probability for this specific line is already low (rare occurrence
    regardless of which exact threshold was asked for)."""
    is_rare = (market == "player_disposals" and threshold >= DISPOSAL_RARE_THRESHOLD) or (
        market == "player_goals" and threshold >= GOAL_RARE_THRESHOLD
    )
    if is_rare or model_probability < 0.15:
        return "Rare-event probability — the historical sample for outcomes this uncommon is smaller, so treat calibration here with more caution."
    return None
