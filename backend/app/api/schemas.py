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


# --- Player data foundation (Stage 3) ---

_PLAYER_STAT_FIELD_NAMES = [
    "kicks", "marks", "handballs", "disposals", "goals", "behinds", "hitouts", "tackles",
    "rebound_50s", "inside_50s", "clearances", "clangers", "frees_for", "frees_against",
    "brownlow_votes", "contested_possessions", "uncontested_possessions", "contested_marks",
    "marks_inside_50", "one_percenters", "bounces", "goal_assists",
]


class PlayerSummaryRead(BaseModel):
    id: int
    display_name: str
    current_team: TeamSummary | None
    is_active: bool | None
    source: str
    source_player_id: str

    model_config = {"from_attributes": True}


class PlayerListRead(BaseModel):
    players: list[PlayerSummaryRead]
    total: int
    limit: int
    offset: int


class PlayerGameStatRead(BaseModel):
    match_id: int
    player_id: int
    player_display_name: str
    season_year: int
    round_number: int
    scheduled_start: datetime
    team: TeamSummary
    opponent_team: TeamSummary | None
    jumper_number: int | None
    subbed_on: bool
    subbed_off: bool
    kicks: int | None
    marks: int | None
    handballs: int | None
    disposals: int | None
    goals: int | None
    behinds: int | None
    hitouts: int | None
    tackles: int | None
    rebound_50s: int | None
    inside_50s: int | None
    clearances: int | None
    clangers: int | None
    frees_for: int | None
    frees_against: int | None
    brownlow_votes: int | None
    contested_possessions: int | None
    uncontested_possessions: int | None
    contested_marks: int | None
    marks_inside_50: int | None
    one_percenters: int | None
    bounces: int | None
    goal_assists: int | None
    time_on_ground_pct: int | None
    fantasy_points: float | None


class PlayerGamesRead(BaseModel):
    player: PlayerSummaryRead
    games: list[PlayerGameStatRead]
    total: int
    limit: int
    offset: int


class SeasonAverageRead(BaseModel):
    season_year: int
    games_played: int
    averages: dict[str, float]


class PlayerFormRead(BaseModel):
    player: PlayerSummaryRead
    recent_games: list[PlayerGameStatRead]
    season_averages: list[SeasonAverageRead]


class MatchPlayersRead(BaseModel):
    match_id: int
    home_team_players: list[PlayerGameStatRead]
    away_team_players: list[PlayerGameStatRead]


# --- Player-model (disposal projection) research API ---
# Deliberately marked as historical model research, not live betting advice
# — see app/player_modelling/disposal_cli.py's module docstring and the
# disposal-prediction stage brief's Section 23. Distinct from the
# team-level ModelRun/backtests schemas above, mirroring how
# app/models/player_model_run.py keeps its own tables.


class PlayerModelRunSummaryRead(BaseModel):
    model_name: str
    market: str
    is_promoted: bool
    distribution_method: str
    feature_names: list[str]
    tune_start_year: int
    tune_end_year: int
    evaluation_start_year: int
    evaluation_end_year: int
    run_at: datetime
    overall_mae: float | None
    overall_rmse: float | None
    overall_bias: float | None
    evaluation_n: int | None


class PlayerModelRunListRead(BaseModel):
    is_research_only: bool = True
    runs: list[PlayerModelRunSummaryRead]


class DisposalSeasonMetricRead(BaseModel):
    season_year: int
    n: int
    mae: float
    rmse: float
    bias: float


class DisposalBaselineComparisonRead(BaseModel):
    model_name: str
    mae: float
    rmse: float
    bias: float


class DisposalBacktestSummaryRead(BaseModel):
    is_research_only: bool = True
    promoted_model: PlayerModelRunSummaryRead
    baselines: list[DisposalBaselineComparisonRead]
    candidates: list[DisposalBaselineComparisonRead]
    season_breakdown: list[DisposalSeasonMetricRead]
    within_2: float
    within_5: float
    within_10: float
    median_ae: float


class ReliabilityBucketRead(BaseModel):
    bucket: str
    n: int
    avg_predicted: float | None
    actual_rate: float | None


class DisposalThresholdCalibrationRead(BaseModel):
    threshold: float
    n: int
    brier: float
    log_loss: float
    ece: float | None
    calibration: list[ReliabilityBucketRead]


class DisposalIntervalCalibrationRead(BaseModel):
    coverage_target: float
    n: int
    empirical_coverage: float
    mean_width: float


class DisposalCalibrationReportRead(BaseModel):
    is_research_only: bool = True
    model_name: str
    distribution_method: str
    thresholds: list[DisposalThresholdCalibrationRead]
    intervals: list[DisposalIntervalCalibrationRead]


class DisposalPlayerPredictionRead(BaseModel):
    match_id: int
    season_year: int
    games_of_history: int
    predicted_mean: float
    actual_disposals: int
    confidence_tier: str
    interval_50: tuple[float, float]
    interval_80: tuple[float, float]
    prob_20_plus: float
    prob_25_plus: float
    prob_30_plus: float
    prob_35_plus: float


class DisposalPlayerHistoryRead(BaseModel):
    is_research_only: bool = True
    player: PlayerSummaryRead
    model_name: str
    predictions: list[DisposalPlayerPredictionRead]


# --- Goal-model research API (mirrors the disposal schemas above) ---


class GoalModelRunSummaryRead(BaseModel):
    model_name: str
    market: str
    is_promoted: bool
    distribution_kind: str
    feature_names: list[str]
    tune_start_year: int
    tune_end_year: int
    evaluation_start_year: int
    evaluation_end_year: int
    run_at: datetime
    overall_mae: float | None
    overall_rmse: float | None
    overall_bias: float | None
    evaluation_n: int | None


class GoalSeasonMetricRead(BaseModel):
    season_year: int
    n: int
    mae: float
    bias: float


class GoalBaselineComparisonRead(BaseModel):
    model_name: str
    mae: float | None
    rmse: float | None
    bias: float | None


class ZeroGoalCalibrationRead(BaseModel):
    brier: float
    log_loss: float
    ece: float | None
    mean_predicted_p0: float
    actual_p0: float


class GoalBacktestSummaryRead(BaseModel):
    is_research_only: bool = True
    promoted_model: GoalModelRunSummaryRead
    baselines: list[GoalBaselineComparisonRead]
    candidates: list[GoalBaselineComparisonRead]
    season_breakdown: list[GoalSeasonMetricRead]
    zero_goal: ZeroGoalCalibrationRead


class GoalThresholdCalibrationRead(BaseModel):
    threshold: float
    n: int
    n_positive: int
    brier: float
    log_loss: float
    ece: float | None


class GoalCalibrationReportRead(BaseModel):
    is_research_only: bool = True
    model_name: str
    distribution_kind: str
    thresholds: list[GoalThresholdCalibrationRead]


class GoalPlayerPredictionRead(BaseModel):
    match_id: int
    season_year: int
    games_of_history: int
    predicted_mean: float
    actual_goals: int
    confidence_tier: str
    prob_1_plus: float
    prob_2_plus: float
    prob_3_plus: float
    prob_4_plus: float
    prob_5_plus: float


class GoalPlayerHistoryRead(BaseModel):
    is_research_only: bool = True
    player: PlayerSummaryRead
    model_name: str
    predictions: list[GoalPlayerPredictionRead]


# --- Live player projections (live-projection stage) ---


class ExpectedLineupStatusEnum(str, Enum):
    EXPECTED_IN = "expected_in"
    EXPECTED_OUT = "expected_out"
    UNCERTAIN = "uncertain"


class ExpectedLineupCreate(BaseModel):
    player_id: int
    team_id: int
    status: ExpectedLineupStatusEnum
    note: str | None = Field(default=None, max_length=500)
    substitute_risk: bool = False
    returning_from_injury: bool = False
    role_note: str | None = Field(default=None, max_length=200)
    expected_tog_adjustment: float | None = None


class ExpectedLineupRead(BaseModel):
    id: int
    match_id: int
    player_id: int
    player_name: str
    team_id: int
    status: str
    recorded_at: datetime
    source: str
    note: str | None
    substitute_risk: bool
    returning_from_injury: bool
    role_note: str | None
    expected_tog_adjustment: float | None


class ThresholdProbabilityRead(BaseModel):
    probability: float
    warning: str | None


class DisposalProjectionRead(BaseModel):
    is_research_only: bool = False
    match_id: int
    player_id: int
    player_name: str
    team_id: int
    team_name: str
    round_number: int
    season_year: int
    scheduled_start: datetime
    model_name: str
    model_version: str
    generated_at: datetime
    data_cutoff: datetime
    lineup_status: str
    games_of_history: int
    expected: float
    median: float
    interval_50: tuple[float, float]
    interval_80: tuple[float, float]
    interval_90: tuple[float, float]
    thresholds: dict[str, ThresholdProbabilityRead]
    confidence_tier: str
    warnings: list[str]
    is_stale: bool
    stale_reasons: list[str]
    input_features: dict[str, float | None]


class GoalProjectionRead(BaseModel):
    is_research_only: bool = False
    match_id: int
    player_id: int
    player_name: str
    team_id: int
    team_name: str
    round_number: int
    season_year: int
    scheduled_start: datetime
    model_name: str
    model_version: str
    generated_at: datetime
    data_cutoff: datetime
    lineup_status: str
    games_of_history: int
    expected: float
    thresholds: dict[str, ThresholdProbabilityRead]
    scoring_archetype: str
    confidence_tier: str
    warnings: list[str]
    is_stale: bool
    stale_reasons: list[str]
    input_features: dict[str, float | None]


class MatchProjectionsRead(BaseModel):
    match_id: int
    disposals: list[DisposalProjectionRead]
    goals: list[GoalProjectionRead]


class PlayerProjectionRead(BaseModel):
    disposals: DisposalProjectionRead | None
    goals: GoalProjectionRead | None


class PlayerPropMarketCreate(BaseModel):
    bookmaker_name: str = Field(..., min_length=1, max_length=64)
    player_id: int
    market_type: str = Field(..., description="'player_disposals' | 'player_goals'")
    line_type: str = Field(..., description="'over_under' | 'multi_plus'")
    threshold: float
    price_decimal: float = Field(..., gt=1.0, le=1000.0)
    recorded_at: datetime | None = None

    @model_validator(mode="after")
    def _validate_shape(self) -> "PlayerPropMarketCreate":
        if self.market_type not in ("player_disposals", "player_goals"):
            raise ValueError(f"market_type must be 'player_disposals' or 'player_goals', got {self.market_type!r}")
        if self.line_type not in ("over_under", "multi_plus"):
            raise ValueError(f"line_type must be 'over_under' or 'multi_plus', got {self.line_type!r}")
        return self


class PlayerPropMarketRead(BaseModel):
    id: int
    match_id: int
    player_id: int
    player_name: str
    bookmaker_name: str
    market_type: str
    line_type: str
    threshold: float
    price_decimal: float
    recorded_at: datetime
    source: str


class PropInsightRead(BaseModel):
    id: int
    player_id: int
    player_name: str
    match_id: int
    round_number: int
    season_year: int
    bookmaker_name: str
    market_type: str
    line_type: str
    threshold: float
    recorded_at: datetime
    model_probability: float
    model_fair_odds: float
    offered_odds: float
    raw_implied_probability: float
    devigged_probability: float | None
    overround_removed: bool
    difference_pp: float
    expected_value: float
    edge_category: str
    confidence_tier: str
    warnings: list[str]


class GoalTeamDiagnosticRead(BaseModel):
    match_id: int
    team_id: int
    sum_predicted_goals: float
    team_expected_goals: float
    gap: float
