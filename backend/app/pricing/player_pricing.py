"""Player-market pricing (disposals/goals) — pure model belief, independent
of any bookmaker. Reuses the exact same persisted projection rows and
distribution-reconstruction helpers already built for the live-projection
API (see app/player_modelling/live_report_query.py's
disposal_distribution_for/goal_distribution_for/price_line/
historical_calibration_metrics) — nothing here re-fits a model or
duplicates that reconstruction logic.

Reading a PERSISTED PlayerDisposalProjection/PlayerGoalProjection row
(rather than calling generate_live_projections) is a deliberate
performance choice, not just convenience: re-fitting the disposal/goal
regression against the full historical dataset costs ~70-80s per call
(see app/player_modelling/request_cache.py's cached_model_fit docstring) —
unaffordable in a pricing API's request path. Those rows are already kept
current by the live cycle (see live_cycle.py's regenerate_projections
step) whenever lineup/data actually changes, so a pricing read is a cheap
row lookup plus evaluating an already-fitted distribution's PMF at
whatever threshold was requested — no retraining, ever, in this path.
"""

from dataclasses import dataclass, field

from app.edges.fair_odds import fair_odds_from_probability
from app.models import PlayerDisposalProjection, PlayerGoalProjection
from app.player_modelling.live_report_query import (
    CalibrationMetrics,
    current_disposal_model_version,
    current_goal_model_version,
    current_lineup_for,
    disposal_distribution_for,
    goal_distribution_for,
    historical_calibration_metrics,
    price_line,
)
from app.player_modelling.live_staleness import check_staleness
from app.player_modelling.market import PlayerMarket
from app.player_modelling.usage_regime import USAGE_REGIME_CHANGE_FLAG, ModelRiskFlag, goal_usage_risk_flags  # noqa: F401 - re-exported for app.pricing.player_pricing.USAGE_REGIME_CHANGE_FLAG callers

DISPOSAL_MODEL_NAME = "disposal_nb"
GOAL_MODEL_NAME = "goal_hurdle"

_goal_model_risk_flags = goal_usage_risk_flags  # local alias kept for readability at call sites below

# The standard preset threshold set every response includes for free,
# alongside whatever arbitrary threshold(s) the caller explicitly asked
# for (item 1: "probability for arbitrary supported thresholds" - preset
# is a convenience default, not a limit on what can be priced).
DEFAULT_DISPOSAL_THRESHOLDS: tuple[float, ...] = (15.5, 20.5, 25.5, 30.5, 35.5)
DEFAULT_GOAL_THRESHOLDS: tuple[float, ...] = (0.5, 1.5, 2.5, 3.5)


@dataclass(frozen=True)
class ThresholdPrice:
    threshold: float
    line_type: str
    probability: float
    fair_odds: float


def _threshold_price(dist, threshold: float, line_type: str = "over_under") -> ThresholdPrice:
    p = price_line(dist, threshold, line_type)
    return ThresholdPrice(
        threshold=threshold, line_type=line_type, probability=p,
        fair_odds=fair_odds_from_probability(p) if p > 0 else float("inf"),
    )


@dataclass(frozen=True)
class DisposalPrice:
    match_id: int
    player_id: int
    player_name: str
    team_id: int
    model_name: str
    model_version: str
    generated_at: object
    data_cutoff: object
    lineup_status: str
    confidence_tier: str
    games_of_history: int
    expected: float
    distribution_method: str
    distribution_params: dict
    interval_50: tuple
    interval_80: tuple
    interval_90: tuple
    thresholds: list[ThresholdPrice] = field(default_factory=list)
    calibration: CalibrationMetrics | None = None
    warnings: list[str] = field(default_factory=list)
    is_stale: bool = False
    stale_reasons: list[str] = field(default_factory=list)
    usage_regime: str | None = None
    usage_change_score: float | None = None
    model_risk_flags: list[ModelRiskFlag] = field(default_factory=list)


def price_disposals(db, row: PlayerDisposalProjection, extra_thresholds: list[float] | None = None) -> DisposalPrice:
    dist = disposal_distribution_for(row)
    thresholds = [_threshold_price(dist, t) for t in DEFAULT_DISPOSAL_THRESHOLDS]
    thresholds += [_threshold_price(dist, t) for t in (extra_thresholds or [])]
    # The nearest preset threshold to the model's own mean is the most
    # representative single number for historical-calibration lookup.
    nearest_default = min(DEFAULT_DISPOSAL_THRESHOLDS, key=lambda t: abs(t - row.predicted_mean))

    current_lineup = current_lineup_for(db, row.player_id, row.match_id)
    staleness = check_staleness(
        projection_model_version=row.model_version, projection_data_cutoff=row.data_cutoff,
        projection_lineup_status=row.lineup_status_at_generation, current_model_version=current_disposal_model_version(db),
        current_data_cutoff=None, current_lineup_status=current_lineup.status if current_lineup else None,
    )

    return DisposalPrice(
        match_id=row.match_id, player_id=row.player_id, player_name=row.player.display_name, team_id=row.team_id,
        model_name=DISPOSAL_MODEL_NAME, model_version=row.model_version,
        generated_at=row.generated_at, data_cutoff=row.data_cutoff, lineup_status=row.lineup_status_at_generation,
        confidence_tier=row.confidence_tier, games_of_history=row.games_of_history, expected=row.predicted_mean,
        distribution_method=row.distribution_method, distribution_params={"mu": row.predicted_mean, "alpha": row.nb_alpha},
        interval_50=dist.interval(0.5), interval_80=dist.interval(0.8), interval_90=dist.interval(0.9),
        thresholds=thresholds,
        calibration=historical_calibration_metrics(db, PlayerMarket.DISPOSALS.value, nearest_default),
        warnings=list(row.warnings or []), is_stale=staleness.is_stale, stale_reasons=staleness.reasons,
        usage_regime=row.usage_regime, usage_change_score=row.usage_change_score,
        # Disposal's historical usage-change effect (~1.7% MAE) did not meet
        # the evidence bar a flag requires (see _goal_model_risk_flags's
        # docstring) — usage_regime is still exposed above as low-priority
        # informational context, but no structured risk flag is raised here.
    )


@dataclass(frozen=True)
class GoalPrice:
    match_id: int
    player_id: int
    player_name: str
    team_id: int
    model_name: str
    model_version: str
    generated_at: object
    data_cutoff: object
    lineup_status: str
    confidence_tier: str
    games_of_history: int
    expected: float
    distribution_kind: str
    distribution_params: dict
    scoring_archetype: str
    thresholds: list[ThresholdPrice] = field(default_factory=list)
    calibration: CalibrationMetrics | None = None
    warnings: list[str] = field(default_factory=list)
    is_stale: bool = False
    stale_reasons: list[str] = field(default_factory=list)
    usage_regime: str | None = None
    usage_change_score: float | None = None
    model_risk_flags: list[ModelRiskFlag] = field(default_factory=list)


def price_goals(db, row: PlayerGoalProjection, extra_thresholds: list[float] | None = None) -> GoalPrice:
    dist = goal_distribution_for(row)
    thresholds = [_threshold_price(dist, t) for t in DEFAULT_GOAL_THRESHOLDS]
    thresholds += [_threshold_price(dist, t) for t in (extra_thresholds or [])]
    params = (
        {"p_score": row.p_score, "mu_scored": row.mu_scored, "alpha_scored": row.alpha_scored}
        if row.distribution_kind == "hurdle"
        else {"mu": row.predicted_mean, "alpha": row.nb_alpha}
    )

    current_lineup = current_lineup_for(db, row.player_id, row.match_id)
    staleness = check_staleness(
        projection_model_version=row.model_version, projection_data_cutoff=row.data_cutoff,
        projection_lineup_status=row.lineup_status_at_generation, current_model_version=current_goal_model_version(db),
        current_data_cutoff=None, current_lineup_status=current_lineup.status if current_lineup else None,
    )

    return GoalPrice(
        match_id=row.match_id, player_id=row.player_id, player_name=row.player.display_name, team_id=row.team_id,
        model_name=GOAL_MODEL_NAME, model_version=row.model_version,
        generated_at=row.generated_at, data_cutoff=row.data_cutoff, lineup_status=row.lineup_status_at_generation,
        confidence_tier=row.confidence_tier, games_of_history=row.games_of_history, expected=row.predicted_mean,
        distribution_kind=row.distribution_kind, distribution_params=params, scoring_archetype=row.scoring_archetype,
        thresholds=thresholds,
        calibration=historical_calibration_metrics(db, PlayerMarket.GOALS.value, 1.5),
        warnings=list(row.warnings or []), is_stale=staleness.is_stale, stale_reasons=staleness.reasons,
        usage_regime=row.usage_regime, usage_change_score=row.usage_change_score,
        model_risk_flags=_goal_model_risk_flags(row.usage_regime),
    )
