"""Pydantic request/response models for the API layer.

Kept separate from the SQLAlchemy models (app/models/) so the API's public
shape can evolve independently of the DB schema — e.g. nesting team/venue
details inline in a match response without that shape leaking back into the
ORM layer.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator


class TeamSummary(BaseModel):
    id: int
    name: str
    short_name: str
    primary_colour: str | None
    secondary_colour: str | None

    model_config = {"from_attributes": True}


class VenueSummary(BaseModel):
    id: int
    name: str
    city: str | None

    model_config = {"from_attributes": True}


class SeasonSummary(BaseModel):
    id: int
    year: int

    model_config = {"from_attributes": True}


class MatchSummary(BaseModel):
    id: int
    season_year: int
    round_number: int
    status: str
    scheduled_start: datetime
    home_team: TeamSummary
    away_team: TeamSummary
    venue: VenueSummary | None
    home_score: int | None
    away_score: int | None


class MarketType(str, Enum):
    H2H = "h2h"
    LINE = "line"
    TOTAL = "total"


class OddsQuoteCreate(BaseModel):
    bookmaker_name: str = Field(..., min_length=1, max_length=64)
    market_type: MarketType
    selection: str = Field(..., min_length=1, max_length=64)
    line_value: float | None = None
    price_decimal: float = Field(..., gt=1.0, le=1000.0)
    recorded_at: datetime | None = None
    is_closing_line: bool = False

    @model_validator(mode="after")
    def _validate_market_shape(self) -> "OddsQuoteCreate":
        if self.market_type in (MarketType.LINE, MarketType.TOTAL) and self.line_value is None:
            raise ValueError(f"line_value is required for market_type={self.market_type.value!r}")
        if self.market_type == MarketType.H2H and self.line_value is not None:
            raise ValueError("line_value must not be set for market_type='h2h'")
        if self.market_type == MarketType.TOTAL and self.selection.lower() not in ("over", "under"):
            raise ValueError("selection must be 'over' or 'under' for market_type='total'")
        return self


class OddsQuoteRead(BaseModel):
    id: int
    match_id: int
    bookmaker_name: str
    market_type: str
    selection: str
    line_value: float | None
    price_decimal: float
    recorded_at: datetime
    source: str
    is_closing_line: bool


class MarketEdgeRead(BaseModel):
    match_id: int
    odds_quote_id: int
    market_type: str
    selection: str
    line_value: float | None
    bookmaker_name: str
    price_decimal: float
    model_probability: float
    secondary_model_probability: float | None
    market_implied_probability: float
    fair_market_probability: float
    overround_removed: bool
    fair_odds: float
    model_edge: float
    expected_value: float
    edge_tier: str
    confidence_tier: str
    confidence_reasons: list[str]

    model_config = {"from_attributes": True}


class MatchPredictionsRead(BaseModel):
    match_id: int
    elo_home_win_probability: float
    poisson_home_win_probability: float
    poisson_draw_probability: float
    poisson_away_win_probability: float
    poisson_home_expected_score: float
    poisson_away_expected_score: float
    poisson_expected_total_points: float
    poisson_expected_margin: float

    model_config = {"from_attributes": True}


class DashboardEntry(BaseModel):
    match: MatchSummary
    # None when the Elo/Poisson modelling CLIs haven't been run yet — the
    # match itself should still show, per the model/data separation rule.
    predictions: MatchPredictionsRead | None
    best_edge: MarketEdgeRead | None


class BacktestSegmentRead(BaseModel):
    label: str
    n: int
    metrics: dict[str, float]

    model_config = {"from_attributes": True}


class CalibrationBucketRead(BaseModel):
    bucket: str
    n: int
    avg_predicted: float | None
    actual_rate: float | None


class WinProbReportRead(BaseModel):
    model_name: str
    overall: BacktestSegmentRead
    by_season: list[BacktestSegmentRead]
    by_team: list[BacktestSegmentRead]
    by_conviction: list[BacktestSegmentRead]
    calibration: list[CalibrationBucketRead]

    model_config = {"from_attributes": True}


class ScoringReportRead(BaseModel):
    overall: BacktestSegmentRead
    by_season: list[BacktestSegmentRead]

    model_config = {"from_attributes": True}


class TrackedSelectionRead(BaseModel):
    match_id: int
    market_type: str
    selection: str
    line_value: float | None
    bookmaker_name: str
    price_decimal: float
    is_closing_line: bool
    model_probability: float
    won: bool | None
    pnl_units: float

    model_config = {"from_attributes": True}


class LoggedOddsReportRead(BaseModel):
    n_total: int
    n_resolved: int
    n_void: int
    win_rate: float | None
    roi_pct: float | None
    yield_pct: float | None
    total_pnl_units: float
    brier_score: float | None
    log_loss: float | None
    selections: list[TrackedSelectionRead]

    model_config = {"from_attributes": True}


class BacktestOverview(BaseModel):
    elo: WinProbReportRead
    poisson_win: WinProbReportRead
    poisson_scoring: ScoringReportRead
    logged_odds: LoggedOddsReportRead


# --- Model evaluation / rigorous backtesting (Stage 1B) ---
# Deliberately separate from the WinProbReportRead/BacktestOverview family
# above: those validate a model's own history end-to-end with no comparison
# point, these evaluate predictive *quality* against baselines and each
# other over a fixed warm-up-excluded evaluation window. See
# app/backtesting/evaluation.py's module docstring for why this is a
# different question from app/backtesting/model_report.py's.


class EvaluationPeriodRead(BaseModel):
    warmup_start_year: int
    warmup_end_year: int
    evaluation_start_year: int
    evaluation_end_year: int
    current_season_year: int
    n_warmup: int
    n_evaluation: int
    n_current_season: int

    model_config = {"from_attributes": True}


class BaselineComparisonRowRead(BaseModel):
    name: str
    n: int
    metrics: dict[str, float]

    model_config = {"from_attributes": True}


class WinProbEvaluationRead(BaseModel):
    model_name: str
    period: EvaluationPeriodRead
    evaluation_metrics: dict[str, float]
    warmup_metrics: dict[str, float]
    full_history_metrics: dict[str, float]
    baseline_comparison: list[BaselineComparisonRowRead]
    calibration: list[CalibrationBucketRead]
    calibration_ece: float | None
    by_season: list[BacktestSegmentRead]

    model_config = {"from_attributes": True}


class ScoringEvaluationRead(BaseModel):
    period: EvaluationPeriodRead
    evaluation_metrics: dict[str, float]
    warmup_metrics: dict[str, float]
    full_history_metrics: dict[str, float]
    by_season: list[BacktestSegmentRead]
    interval_coverage: dict[str, dict[str, float]]

    model_config = {"from_attributes": True}


class BacktestSummaryRead(BaseModel):
    id: str
    model_name: str
    run_at: datetime
    tune_end_year: int
    evaluation_start_year: int
    n_evaluation: int
    headline_metrics: dict[str, float]


class BacktestDetailRead(BaseModel):
    id: str
    model_name: str
    config: dict
    tune_end_year: int
    run_at: datetime
    win_prob: WinProbEvaluationRead
    scoring: ScoringEvaluationRead | None


class CalibrationReportRead(BaseModel):
    model_name: str
    buckets: list[CalibrationBucketRead]
    ece: float | None


class SeasonBreakdownRead(BaseModel):
    model_name: str
    win_prob_by_season: list[BacktestSegmentRead]
    scoring_by_season: list[BacktestSegmentRead] | None


class DisagreementBucketRead(BaseModel):
    label: str
    n: int
    elo_metrics: dict[str, float]
    poisson_metrics: dict[str, float]
    actual_home_win_rate: float | None

    model_config = {"from_attributes": True}


class SeasonStabilityRowRead(BaseModel):
    season_year: str
    n_games: int
    elo_accuracy: float
    elo_brier: float
    elo_log_loss: float
    poisson_total_mae: float
    poisson_margin_mae: float
    home_win_rate: float

    model_config = {"from_attributes": True}


class ModelComparisonRead(BaseModel):
    n_matches: int
    overall_elo_metrics: dict[str, float]
    overall_poisson_metrics: dict[str, float]
    mean_absolute_disagreement: float
    disagreement_buckets: list[DisagreementBucketRead]
    season_stability: list[SeasonStabilityRowRead]

    model_config = {"from_attributes": True}


# --- Advanced feature engineering / logistic regression (Stage 1C) ---


class BaselineModelRowRead(BaseModel):
    name: str
    n: int
    brier_score: float
    log_loss: float
    accuracy: float

    model_config = {"from_attributes": True}


class AblationResultRead(BaseModel):
    label: str
    feature_names: list[str]
    n_eval: int
    brier_score: float
    log_loss: float
    brier_vs_elo_alone: float | None

    model_config = {"from_attributes": True}


class BootstrapResultRead(BaseModel):
    point_estimate: float
    ci_low: float
    ci_high: float
    n_resamples: int
    excludes_zero: bool

    model_config = {"from_attributes": True}


class PromotionDecisionRead(BaseModel):
    promote: bool
    reasons: list[str]

    model_config = {"from_attributes": True}


class LogisticDisagreementBucketRead(BaseModel):
    label: str
    n: int
    elo_metrics: dict[str, float]
    logistic_metrics: dict[str, float]
    actual_home_win_rate: float | None

    model_config = {"from_attributes": True}


class LogisticComparisonReportRead(BaseModel):
    n_matches: int
    mean_absolute_disagreement: float
    disagreement_buckets: list[LogisticDisagreementBucketRead]

    model_config = {"from_attributes": True}


class LogisticVariantReportRead(BaseModel):
    variant: str
    feature_names: list[str]
    C: float
    calibration_method: str
    n_eval: int
    brier_score: float
    log_loss: float
    accuracy: float
    calibration: list[CalibrationBucketRead]
    calibration_ece: float | None
    standardized_coefficients: dict[str, float]
    permutation_importance: dict[str, float]
    single_feature_ablation: dict[str, float]
    feature_group_ablation: list[AblationResultRead]
    bootstrap_vs_elo: BootstrapResultRead
    by_season: list[BacktestSegmentRead]
    disagreement_vs_elo: LogisticComparisonReportRead
    promotion: PromotionDecisionRead

    model_config = {"from_attributes": True}


class LogisticComparisonOverviewRead(BaseModel):
    n_eval: int
    evaluation_start_year: int
    evaluation_end_year: int
    baselines: list[BaselineModelRowRead]
    elo: BaselineModelRowRead
    poisson: BaselineModelRowRead
    stats_only: LogisticVariantReportRead
    stats_plus_elo: LogisticVariantReportRead

    model_config = {"from_attributes": True}


# --- Gradient boosting + ensemble (Stage 2A) ---


class FeatureSetCandidateResultRead(BaseModel):
    label: str
    library: str
    feature_names: list[str]
    n_eval: int
    brier_score: float
    log_loss: float
    accuracy: float

    model_config = {"from_attributes": True}


class BoostingAblationResultRead(BaseModel):
    label: str
    feature_names: list[str]
    n_eval: int
    brier_score: float
    log_loss: float
    brier_vs_elo_alone: float | None

    model_config = {"from_attributes": True}


class InteractionFindingRead(BaseModel):
    feature_a: str
    feature_b: str
    label: str
    mean_abs_interaction: float
    mean_abs_main_effect_a: float
    mean_abs_main_effect_b: float

    model_config = {"from_attributes": True}


class BoostingBestCandidateReportRead(BaseModel):
    label: str
    library: str
    feature_names: list[str]
    hyperparameters: dict
    calibration_method: str
    n_eval: int
    brier_score: float
    log_loss: float
    accuracy: float
    calibration: list[CalibrationBucketRead]
    calibration_ece: float | None
    permutation_importance: dict[str, float]
    shap_importance: dict[str, float] | None
    feature_group_ablation: list[BoostingAblationResultRead]
    bootstrap_vs_elo: BootstrapResultRead
    by_season: list[BacktestSegmentRead]
    disagreement_vs_elo: LogisticComparisonReportRead
    interactions: list[InteractionFindingRead]
    promotion: PromotionDecisionRead

    model_config = {"from_attributes": True}


class EnsembleReportRead(BaseModel):
    boosting_weight: float
    elo: BaselineModelRowRead
    boosting: BaselineModelRowRead
    ensemble: BaselineModelRowRead
    bootstrap_ensemble_vs_elo: BootstrapResultRead
    bootstrap_ensemble_vs_boosting: BootstrapResultRead
    use_ensemble: bool

    model_config = {"from_attributes": True}


class BoostingComparisonOverviewRead(BaseModel):
    n_eval: int
    evaluation_start_year: int
    evaluation_end_year: int
    feature_set_candidates: list[FeatureSetCandidateResultRead]
    best: BoostingBestCandidateReportRead
    ensemble: EnsembleReportRead

    model_config = {"from_attributes": True}


# --- Poisson season-transition revision (Stage 2B) ---


class PoissonConfigRead(BaseModel):
    rolling_window_games: int
    min_games_for_reliable_strength: int
    min_league_games_for_home_split: int
    max_goals: int
    max_behinds: int
    league_window_games: int | None

    model_config = {"from_attributes": True}


class RoundBandMetricsRead(BaseModel):
    label: str
    n: int
    metrics: dict[str, float]

    model_config = {"from_attributes": True}


class PoissonVariantReportRead(BaseModel):
    label: str
    config: PoissonConfigRead
    period: EvaluationPeriodRead
    evaluation_metrics: dict[str, float]
    warmup_metrics: dict[str, float]
    full_history_metrics: dict[str, float]
    by_season: list[BacktestSegmentRead]
    early_season_bands: list[RoundBandMetricsRead]
    season_2021_bands: list[RoundBandMetricsRead]
    interval_coverage: dict[str, dict[str, float]]

    model_config = {"from_attributes": True}


class TuneLeaderboardRowRead(BaseModel):
    config: PoissonConfigRead
    tune_total_points_mae: float

    model_config = {"from_attributes": True}


class PoissonRevisionComparisonRead(BaseModel):
    original: PoissonVariantReportRead
    revised: PoissonVariantReportRead
    tune_leaderboard_top5: list[TuneLeaderboardRowRead]
    common_match_count: int
    revised_beats_original_2021: bool
    revised_worse_than_original_full_history: bool
    promotion: PromotionDecisionRead

    model_config = {"from_attributes": True}
