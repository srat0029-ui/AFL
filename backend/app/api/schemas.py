"""Pydantic request/response models for the API layer.

Kept separate from the SQLAlchemy models (app/models/) so the API's public
shape can evolve independently of the DB schema — e.g. nesting team/venue
details inline in a match response without that shape leaking back into the
ORM layer.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, PlainSerializer, model_validator


def _serialize_as_utc(dt: datetime) -> str:
    """Every timestamp this app stores is genuinely UTC (see
    app/models/base.py's _utcnow and the DateTime(timezone=True) columns
    throughout), but SQLite has no native tz-aware datetime type — SQLAlchemy's
    SQLite dialect silently drops the tzinfo on round-trip (a real, previously
    -hit bug pattern in this codebase; see e.g. prop_odds_ingestion.py's
    _same_instant() docstring). A naive datetime serialized via plain
    .isoformat() produces a string with no "Z"/offset suffix (e.g.
    "2026-08-22T03:15:00"), which every major JS engine's `Date` constructor
    then parses as LOCAL time, not UTC — corrupting every timestamp the
    frontend displays by the browser's own UTC offset. This serializer is the
    single, global fix: attach UTC explicitly before serializing, so every API
    response's timestamps are unambiguous regardless of what the DB round-trip
    did to their tzinfo."""
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    return aware.isoformat()


# Used in place of a bare `datetime` type annotation on every response-model
# field below that represents an application timestamp (all of which are
# genuinely UTC) — see _serialize_as_utc's docstring for why this exists.
UtcDatetime = Annotated[datetime, PlainSerializer(_serialize_as_utc, return_type=str)]


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
    scheduled_start: UtcDatetime
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
    recorded_at: UtcDatetime | None = None
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
    recorded_at: UtcDatetime
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
    run_at: UtcDatetime
    tune_end_year: int
    evaluation_start_year: int
    n_evaluation: int
    headline_metrics: dict[str, float]


class BacktestDetailRead(BaseModel):
    id: str
    model_name: str
    config: dict
    tune_end_year: int
    run_at: UtcDatetime
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
    scheduled_start: UtcDatetime
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
    run_at: UtcDatetime
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
    run_at: UtcDatetime
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


class SelectionStatusEnum(str, Enum):
    PLACEHOLDER = "placeholder"
    NAMED_IN_SQUAD = "named_in_squad"
    CONFIRMED_SELECTED = "confirmed_selected"
    EMERGENCY = "emergency"
    SUBSTITUTE = "substitute"
    CONFIRMED_OUT = "confirmed_out"
    UNCERTAIN = "uncertain"


class ExpectedLineupCreate(BaseModel):
    player_id: int
    team_id: int
    status: ExpectedLineupStatusEnum
    selection_status: SelectionStatusEnum | None = None
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
    selection_status: str
    is_confirmed: bool
    recorded_at: UtcDatetime
    source: str
    source_timestamp: UtcDatetime | None
    source_reference: str | None
    is_manual_override: bool
    note: str | None
    substitute_risk: bool
    returning_from_injury: bool
    role_note: str | None
    expected_tog_adjustment: float | None


class RosterSuggestionRead(BaseModel):
    player_id: int
    display_name: str
    last_match_id: int
    last_played_at: UtcDatetime


class BulkApplyEntry(BaseModel):
    player_id: int
    team_id: int
    selection_status: SelectionStatusEnum
    note: str | None = None


class BulkApplyRequest(BaseModel):
    entries: list[BulkApplyEntry]
    source: str = Field(default="manual_bulk", max_length=32)
    allow_override_manual: bool = False


class BulkApplyResult(BaseModel):
    created: list[int]
    updated: list[int]
    status_changed: list[tuple[int, str, str]]
    skipped_manual_override: list[int]
    unresolved: list[str]
    ambiguous: list[str]


class BulkRemoveRequest(BaseModel):
    player_ids: list[int]


class BulkRemoveResult(BaseModel):
    removed: list[int]
    not_found: list[int]


class LineupSummaryRead(BaseModel):
    match_id: int
    announcement_state: str
    n_confirmed_selected: int
    n_named_in_squad: int
    n_emergency: int
    n_substitute: int
    n_confirmed_out: int
    n_uncertain: int
    n_placeholder: int
    n_manual_overrides: int
    last_updated: UtcDatetime | None


class ThresholdProbabilityRead(BaseModel):
    probability: float
    warning: str | None


# --- Current Context + Team News Intelligence stage --------------------


class ContextTypeEnum(str, Enum):
    CONFIRMED_IN = "confirmed_in"
    CONFIRMED_OUT = "confirmed_out"
    INJURY = "injury"
    LATE_WITHDRAWAL = "late_withdrawal"
    NAMED_SUBSTITUTE = "named_substitute"
    EMERGENCY = "emergency"
    RETURNING_PLAYER = "returning_player"
    LIMITED_GAME_TIME_CONCERN = "limited_game_time_concern"
    WEATHER = "weather"
    VENUE_CONDITION = "venue_condition"
    MAJOR_ROLE_CHANGE = "major_role_change"
    OTHER = "other"


class ContextConfidenceEnum(str, Enum):
    OFFICIAL = "official"
    REPUTABLE_SOURCE = "reputable_source"
    UNVERIFIED = "unverified"


class MatchContextItemCreate(BaseModel):
    context_type: ContextTypeEnum
    source: str = Field(max_length=64)
    summary: str = Field(max_length=500)
    confidence: ContextConfidenceEnum = ContextConfidenceEnum.UNVERIFIED
    team_id: int | None = None
    player_id: int | None = None
    source_timestamp: UtcDatetime | None = None
    source_reference: str | None = Field(default=None, max_length=300)
    # Section 7: when true AND context_type maps onto a lineup selection
    # status (confirmed_in/out, named_substitute, emergency, late_withdrawal)
    # AND player_id is set, also updates the existing ExpectedLineup row via
    # the same team_selection_ingestion.py machinery the bulk lineup workflow
    # uses - never a second, separate write path.
    apply_to_lineup: bool = False


class MatchContextItemRead(BaseModel):
    id: int
    match_id: int
    team_id: int | None
    player_id: int | None
    player_name: str | None
    context_type: str
    context_type_label: str
    confidence: str
    confidence_label: str
    source: str
    source_reference: str | None
    source_timestamp: UtcDatetime | None
    recorded_at: UtcDatetime
    summary: str
    freshness: str
    is_current: bool


class MatchContextApplyResult(BaseModel):
    item: MatchContextItemRead
    lineup_updated: bool
    lineup_apply_note: str | None = None


class WeatherSnapshotRead(BaseModel):
    match_id: int
    venue_id: int
    fetched_at: UtcDatetime
    forecast_for: UtcDatetime
    temperature_c: float | None
    rain_probability_pct: float | None
    expected_rainfall_mm: float | None
    wind_speed_kph: float | None
    wind_gust_kph: float | None
    severe_weather_warning: bool
    severe_weather_note: str | None
    source: str


class WeatherRefreshResult(BaseModel):
    matches_considered: int
    snapshots_created: int
    skipped_no_venue: list[int]
    skipped_no_coordinates: list[int]
    skipped_too_far_out: list[int]
    errors: list[str]


class WeatherDiagnosticRead(BaseModel):
    match_id: int
    weather_available: bool
    rain_probability_pct: float | None
    wind_gust_kph: float | None
    is_wet: bool
    is_windy: bool
    projected_total_points: float | None
    historical_sample_overall: int
    historical_mae_overall: float | None
    historical_sample_similar_condition: int
    historical_mae_similar_condition: float | None
    has_sufficient_data: bool
    note: str


class ContextConflictRead(BaseModel):
    codes: list[str]
    labels: list[str]
    latest_context_at: UtcDatetime | None
    model_generated_at: UtcDatetime | None


class MatchContextPanelRead(BaseModel):
    match_id: int
    current_context: list[MatchContextItemRead]
    weather: WeatherSnapshotRead | None
    last_updated: UtcDatetime | None


class RoundContextMatchRead(BaseModel):
    match_id: int
    round_number: int
    season_year: int
    scheduled_start: UtcDatetime
    home_team_name: str
    away_team_name: str
    lineup_announcement_state: str
    n_confirmed_in: int
    n_confirmed_out: int
    n_substitutes: int
    n_other_context_items: int
    weather: WeatherSnapshotRead | None
    n_stale_projections: int


class RoundContextDashboardRead(BaseModel):
    round_number: int | None
    season_year: int | None
    matches: list[RoundContextMatchRead]


class DisposalProjectionRead(BaseModel):
    is_research_only: bool = False
    match_id: int
    player_id: int
    player_name: str
    team_id: int
    team_name: str
    round_number: int
    season_year: int
    scheduled_start: UtcDatetime
    model_name: str
    model_version: str
    generated_at: UtcDatetime
    data_cutoff: UtcDatetime
    lineup_status: str
    selection_status: str
    is_confirmed: bool
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
    scheduled_start: UtcDatetime
    model_name: str
    model_version: str
    generated_at: UtcDatetime
    data_cutoff: UtcDatetime
    lineup_status: str
    selection_status: str
    is_confirmed: bool
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
    # Section 10 of the Current Context stage: selection status/injury/
    # returning context/substitute risk/TOG stability/role note alongside
    # the projection itself, not a separate lookup.
    current_context: list[MatchContextItemRead] = []
    tog_volatile: bool | None = None
    substitute_risk: bool | None = None
    returning_from_injury: bool | None = None
    role_note: str | None = None


class PlayerPropMarketCreate(BaseModel):
    bookmaker_name: str = Field(..., min_length=1, max_length=64)
    player_id: int
    market_type: str = Field(..., description="'player_disposals' | 'player_goals'")
    line_type: str = Field(..., description="'over_under' | 'multi_plus'")
    threshold: float
    price_decimal: float = Field(..., gt=1.0, le=1000.0)
    recorded_at: UtcDatetime | None = None

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
    recorded_at: UtcDatetime
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
    recorded_at: UtcDatetime
    source: str
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
    selection_status: str
    is_confirmed: bool
    warnings: list[str]


class BookmakerQuoteRead(BaseModel):
    bookmaker_name: str
    price_decimal: float
    recorded_at: UtcDatetime
    freshness: str  # "fresh" | "aging" | "stale"
    source: str
    is_exchange: bool
    eligibility: str  # "included" | "excluded" | "informational_only"


class PriceMovementRead(BaseModel):
    first_price: float
    current_price: float
    highest_price: float
    lowest_price: float
    last_movement_at: UtcDatetime


class CalibrationMetricsRead(BaseModel):
    """Numeric ECE/threshold/n behind a market's historical calibration
    context (Section 12 of the best-bets stage brief) - the structured
    counterpart to the pre-formatted note string, for rendering a
    per-threshold calibration display rather than parsing prose."""

    evaluated_threshold: float
    ece: float
    n: int


class OpportunityComponentsRead(BaseModel):
    difference: float
    expected_value: float
    confidence: float
    freshness: float
    lineup: float
    calibration: float
    penalty_multiplier: float
    penalty_reasons: list[str]


class PriceIntegrityCheckRead(BaseModel):
    price_advantage_pct: float
    band_pct: float
    best_bookmaker: str
    best_price: float
    best_price_freshness: str
    next_best_bookmaker: str
    next_best_price: float
    next_best_price_freshness: str
    recorded_at_gap_seconds: float
    passes_integrity: bool
    checks: dict[str, bool]
    issues: list[str]


class MarketMaturityRead(BaseModel):
    tier: str  # "early_market" | "developing_market" | "mature_market"
    label: str
    n_bookmakers: int
    snapshot_count: int | None
    hours_until_kickoff: float | None


class QualityTierRead(BaseModel):
    tier: str  # "strong_candidate" | "worth_reviewing" | "speculative" | "do_not_headline"
    label: str
    caveats: list[str]


class PriceShoppingRead(BaseModel):
    best_enabled: BookmakerQuoteRead | None
    next_best_enabled: BookmakerQuoteRead | None
    worst_enabled: BookmakerQuoteRead | None


class NormalizedPropInsightRead(BaseModel):
    """Best-price, multi-bookmaker view of one normalized player-prop market
    (Sections 12-23 of the automated-odds stage) — distinct from
    PropInsightRead, which is the per-quote (single bookmaker/snapshot)
    view manual entry has always used."""

    match_id: int
    round_number: int
    season_year: int
    player_id: int
    player_name: str
    market_type: str
    line_type: str
    threshold: float
    team_id: int | None = None
    model_probability: float
    model_fair_odds: float
    best_price: float
    best_bookmaker: str
    best_price_is_exchange: bool = False
    eligible_price_available: bool = True
    best_price_all_bookmakers: float | None = None
    best_bookmaker_all_bookmakers: str | None = None
    best_price_all_differs_from_enabled: bool = False
    price_shopping: PriceShoppingRead | None = None
    raw_implied_probability: float
    devigged_probability: float | None
    overround_removed: bool
    difference_pp: float
    expected_value: float
    edge_category: str
    confidence_tier: str
    selection_status: str
    is_confirmed: bool
    warnings: list[str]
    n_bookmakers: int
    bookmakers: list[BookmakerQuoteRead]
    snapshot_count: int | None = None
    odds_freshness: str
    price_movement: PriceMovementRead
    why_model_likes_it: str
    calibration: CalibrationMetricsRead | None
    opportunity_score: float
    opportunity_components: OpportunityComponentsRead


class BestOpportunityRead(BaseModel):
    """One entry in the round-wide Best Opportunities list (Sections 6-11,
    17-18 of the best-bets stage brief) - a player-market or team-market
    row using the SAME transparent opportunity_score/opportunity_components
    as NormalizedPropInsightRead, so both can be ranked and rendered
    together without a second, unexplained ranking formula."""

    opportunity_type: str  # "player" | "team"
    match_id: int
    round_number: int
    season_year: int
    label: str
    market_type: str
    player_id: int | None
    player_name: str | None
    team_id: int | None = None
    line_type: str | None
    threshold: float | None
    selection: str | None
    line_value: float | None
    model_probability: float
    model_fair_odds: float
    best_price: float
    best_bookmaker: str
    best_price_is_exchange: bool = False
    eligible_price_available: bool = True
    best_price_all_bookmakers: float | None = None
    best_bookmaker_all_bookmakers: str | None = None
    best_price_all_differs_from_enabled: bool = False
    price_shopping: PriceShoppingRead | None = None
    quote_source: str | None = None  # team markets only: "the_odds_api" (live) | "manual" (not current)
    market_implied_probability: float
    devigged_probability: float | None
    overround_removed: bool
    difference_pp: float
    expected_value: float
    edge_category: str | None = None
    confidence_tier: str
    selection_status: str | None
    is_confirmed: bool | None
    n_bookmakers: int
    bookmakers: list[BookmakerQuoteRead]
    snapshot_count: int | None = None
    odds_freshness: str
    why_model_likes_it: str
    calibration: CalibrationMetricsRead | None
    warnings: list[str]
    opportunity_score: float
    opportunity_components: OpportunityComponentsRead
    price_integrity: PriceIntegrityCheckRead | None = None
    market_maturity: MarketMaturityRead | None = None
    quality_tier: QualityTierRead | None = None


class OpportunityAlternateLineRead(BaseModel):
    """One alternate line within an opportunity_family, shown under a
    diversified headline (Section 3) rather than as its own Top-10 row."""

    threshold: float | None
    line_type: str | None
    label: str
    model_probability: float
    best_price: float
    best_bookmaker: str
    difference_pp: float
    expected_value: float
    n_bookmakers: int


class RecentFormRead(BaseModel):
    stat_field: str
    last5: list[int]
    last10: list[int]
    last5_avg: float | None
    last10_avg: float | None
    predicted_mean: float | None
    hit_rate_description: str
    form_disagreement_label: str | None
    conservative_model_flag: str | None


class DiversifiedOpportunityRead(BestOpportunityRead):
    """A headline opportunity within a diversified view (Sections 2-6, 12-16
    of the Weekly Opportunity Discovery stage) — everything a
    BestOpportunityRead already has, plus family/diversification context.
    Never a second ranking formula: opportunity_score/opportunity_components
    are the SAME numbers as the raw ranking; representative_score is that
    same score with one documented, visible multiplier applied (see
    opportunity_families.py)."""

    family_label: str
    alternate_lines: list[OpportunityAlternateLineRead]
    correlation_labels: list[str]
    price_advantage_pct: float | None
    recent_form: RecentFormRead | None
    reason_codes: list[str]
    reason_labels: list[str]
    representative_score: float


class WeeklySummaryRead(BaseModel):
    round_number: int | None
    n_opportunities_passing_gates: int
    n_unique_players: int
    n_unique_matches: int
    n_bookmakers: int
    best_difference_pp: float | None
    best_price_advantage_pct: float | None


class BookmakerCoverageRead(BaseModel):
    bookmaker_name: str
    n_active_player_markets: int
    n_matches_covered: int


class DiversifiedOpportunitiesResponseRead(BaseModel):
    opportunities: list[DiversifiedOpportunityRead]
    summary: WeeklySummaryRead
    bookmaker_coverage: list[BookmakerCoverageRead]


class OpportunityTiersResponseRead(BaseModel):
    """Best / Worth Reviewing / All Available — see opportunity_tiers.py."""

    best: list[DiversifiedOpportunityRead]
    worth_reviewing: list[DiversifiedOpportunityRead]
    all_available: list[BestOpportunityRead]
    exclusion_breakdown: dict[str, int]
    n_candidates: int
    n_hard_excluded: int
    fallback_message: str | None


# --- Market Integrity + Final Weekly Picks stage ---------------------------


class BookmakerRead(BaseModel):
    id: int
    name: str
    provider_key: str | None
    region: str | None
    is_exchange: bool
    eligibility: str  # "included" | "excluded" | "informational_only"


class BookmakerEligibilityUpdate(BaseModel):
    eligibility: str  # "included" | "excluded" | "informational_only"


class FinalShortlistOpportunityRead(BestOpportunityRead):
    """One entry in the Final Weekly Shortlist (Sections 7-11) — more
    selective than DiversifiedOpportunityRead: only quality_tier
    strong_candidate/worth_reviewing opportunities ever appear here, at
    most one per strong-correlation group (see market_correlation.py),
    and Top N is a maximum never a manufactured target."""

    family_label: str
    alternate_lines: list[OpportunityAlternateLineRead]
    correlation_labels: list[str]
    reason_codes: list[str]
    why_it_ranks_here: list[str]
    caveats: list[str]


class ExcludedOpportunityRead(BaseModel):
    label: str
    opportunity_type: str
    reason: str


class FinalShortlistResponseRead(BaseModel):
    opportunities: list[FinalShortlistOpportunityRead]
    excluded: list[ExcludedOpportunityRead]
    empty_state_reason: str | None
    any_confirmed_player_lineups: bool


class ModelMarketDisagreementRead(BaseModel):
    """Section 18 — 'Model vs Market Disagreements', explicitly NOT an
    opportunity list and never labelled 'Best Bets'. Surfaces the largest
    model/market gaps in EITHER direction, including cases where the
    market is far more confident than the model (invisible everywhere
    else in this app, since Best Opportunities/Final Shortlist only ever
    show markets where the model exceeds the market)."""

    opportunity_type: str  # "player" | "team"
    match_id: int
    label: str
    market_type: str
    player_id: int | None
    player_name: str | None
    threshold: float | None
    line_type: str | None
    model_probability: float
    model_predicted_mean: float | None
    market_probability: float
    overround_removed: bool
    difference_pp: float
    direction: str  # "model_above_market" | "market_above_model"
    confidence_tier: str
    best_price: float
    best_bookmaker: str
    bookmakers: list[BookmakerQuoteRead]
    n_bookmakers: int
    recent_form: dict | None
    calibration: CalibrationMetricsRead | None
    odds_freshness: str
    warnings: list[str]


class PlayerBiasEntryRead(BaseModel):
    player_id: int
    player_name: str
    n_predictions: int
    avg_actual: float
    avg_predicted: float
    bias: float


class EliteDisposalBucketRead(BaseModel):
    bucket: str
    label: str
    n_players: int
    n_predictions: int
    avg_actual: float
    avg_predicted: float
    bias: float
    mae: float
    most_under_predicted_players: list[PlayerBiasEntryRead]


# --- Weekly Bet Review + Decision Support stage -----------------------------


class ModelStrengthRead(BaseModel):
    market_type: str
    model_name: str
    metrics: dict
    evaluation_sample: int
    caveats: list[str]


class CalibrationBandRead(BaseModel):
    band_label: str
    avg_predicted: float | None
    actual_rate: float | None
    n: int
    meets_min_sample: bool


class DirectionAgreementRead(BaseModel):
    classification: str
    model_favours_selection: bool
    market_favours_selection: bool
    description: str


class ProjectionLineDistanceRead(BaseModel):
    market_type: str
    model_projection: float
    line_value: float
    distance: float
    unit: str


class PricePointRead(BaseModel):
    bookmaker_name: str | None
    price_decimal: float
    model_estimated_ev: float


class PriceSensitivityRead(BaseModel):
    model_fair_price: float
    price_points: list[PricePointRead]


class MarketMovementRead(BaseModel):
    first_price: float
    first_observed_at: UtcDatetime
    latest_price: float
    latest_observed_at: UtcDatetime
    best_current_price: float
    model_fair_odds: float
    direction: str
    description: str


class BookmakerProbabilityRead(BaseModel):
    bookmaker_name: str
    price_decimal: float
    probability: float
    overround_removed: bool


class ConsensusRead(BaseModel):
    consensus_probability: float
    n_bookmakers: int
    n_devigged: int
    spread: float
    methodology: str
    per_bookmaker: list[BookmakerProbabilityRead]


class OutlierCheckRead(BaseModel):
    is_outlier: bool
    best_price: float
    median_eligible_price: float
    pct_difference: float
    message: str | None


class EvidenceSummaryRead(BaseModel):
    evidence_codes: list[str]
    evidence_labels: list[str]
    caution_codes: list[str]
    caution_labels: list[str]


class WeeklyReviewOpportunityRead(BestOpportunityRead):
    """One opportunity anywhere on the Weekly Review page - every Best
    Opportunities field plus this stage's full context. Family/reason-code
    fields are optional since "Markets Waiting on Team Confirmation" draws
    from the raw (non-diversified) ranking, which doesn't compute them."""

    family_label: str | None = None
    alternate_lines: list[OpportunityAlternateLineRead] = []
    correlation_labels: list[str] = []
    reason_codes: list[str] = []
    why_it_ranks_here: list[str] = []
    caveats: list[str] = []

    model_strength: ModelStrengthRead | None = None
    calibration_band: CalibrationBandRead | None = None
    direction_agreement: DirectionAgreementRead
    projection_line_distance: ProjectionLineDistanceRead | None = None
    price_sensitivity: PriceSensitivityRead
    market_movement: MarketMovementRead | None = None
    consensus: ConsensusRead | None = None
    outlier_check: OutlierCheckRead | None = None
    evidence_summary: EvidenceSummaryRead
    current_context: list[MatchContextItemRead] = []
    context_conflict: ContextConflictRead | None = None


class WeeklyReviewPageRead(BaseModel):
    final_shortlist: list[WeeklyReviewOpportunityRead]
    strongest_player_opportunities: list[WeeklyReviewOpportunityRead]
    strongest_team_opportunities: list[WeeklyReviewOpportunityRead]
    model_vs_market_disagreements_count: int
    markets_waiting_on_team_confirmation: list[WeeklyReviewOpportunityRead]
    bookmaker_coverage: list[BookmakerCoverageRead]
    weekly_summary: WeeklySummaryRead
    any_confirmed_player_lineups: bool


class ShortlistSnapshotItemRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    rank: int
    opportunity_type: str
    label: str
    match_id: int
    market_type: str
    player_id: int | None
    selection: str | None
    threshold: float | None
    line_value: float | None
    line_type: str | None
    best_price: float
    best_bookmaker: str
    recorded_at: UtcDatetime
    model_probability: float
    model_fair_odds: float
    market_implied_probability: float
    devigged_probability: float | None
    overround_removed: bool
    difference_pp: float
    expected_value: float
    confidence_tier: str
    quality_tier: str
    market_maturity_tier: str | None
    is_confirmed: bool | None
    model_name: str | None
    model_version: str | None
    n_bookmakers: int
    reasons_json: dict
    actual_stat_value: float | None
    match_result: str | None
    settled_at: UtcDatetime | None


class ShortlistSnapshotRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    created_at: UtcDatetime
    round_number: int | None
    season_year: int | None
    limit_requested: int | None
    include_unconfirmed_players: bool
    n_items: int
    label: str | None
    items: list[ShortlistSnapshotItemRead]


class ShortlistSnapshotSummaryRead(BaseModel):
    """The lightweight row shown in the snapshot history list - full item
    detail is only fetched when one snapshot is opened/replayed."""

    id: int
    created_at: UtcDatetime
    round_number: int | None
    season_year: int | None
    n_items: int
    label: str | None


class CreateSnapshotRequest(BaseModel):
    limit: int | None = None
    include_unconfirmed_players: bool = False
    label: str | None = None


class SettleSnapshotResult(BaseModel):
    snapshot_id: int
    settled_count: int


class ShortlistRoundSummaryItemRead(BaseModel):
    label: str
    opportunity_type: str
    best_price: float
    best_bookmaker: str
    model_probability: float
    market_implied_probability: float
    match_result: str | None
    actual_stat_value: float | None
    flat_stake_pl: float | None


class ShortlistRoundSummaryRead(BaseModel):
    """The Weekly Review Shortlist's own post-round summary (Weekly Bet
    Review stage, Section 17) — settled outcomes against a frozen
    snapshot. Named distinctly from the pre-existing RoundSummaryRead
    (Live Status stage's DATA-COVERAGE round summary — matches/
    projections/quotes counts, an unrelated concept) to avoid clashing
    with that class name."""

    snapshot_id: int
    round_number: int | None
    season_year: int | None
    n_items: int
    n_settled: int
    n_unresolved: int
    n_won: int
    n_lost: int
    n_push: int
    hypothetical_flat_stake_pl: float | None
    n_unique_matches: int
    n_team: int
    n_player: int
    confidence_tier_breakdown: dict[str, int]
    quality_tier_breakdown: dict[str, int]
    small_sample_warning: bool
    items: list[ShortlistRoundSummaryItemRead]


class GoalTeamDiagnosticRead(BaseModel):
    match_id: int
    team_id: int
    sum_predicted_goals: float
    team_expected_goals: float
    gap: float


# --- Real market tracking (Sections 9-15, 20 of the market-logging stage) ---


class DatasetSummaryRead(BaseModel):
    total_observations: int
    settled_observations: int
    pending_observations: int
    unique_player_matches: int
    unique_players: int
    unique_matches: int
    unique_market_lines: int
    bookmakers: list[str]
    earliest_observed_at: UtcDatetime | None
    latest_observed_at: UtcDatetime | None


class ModelVsMarketRead(BaseModel):
    n_settled_binary: int
    model_brier: float | None
    model_log_loss: float | None
    market_brier: float | None
    market_log_loss: float | None
    market_probability_source: str


class CalibrationBucketRead(BaseModel):
    probability_range: str
    n: int
    mean_predicted: float | None
    mean_actual: float | None


class HypotheticalReturnRead(BaseModel):
    n_settled_binary: int
    n_pushed: int
    n_voided: int
    total_profit_flat_stake: float
    roi: float | None
    win_rate: float | None
    average_odds: float | None
    average_model_probability: float | None
    average_difference_pp: float | None


class BucketResultRead(BaseModel):
    label: str
    n_observations: int
    n_unique_player_matches: int
    returns: HypotheticalReturnRead
    sample_size_level: str


class CoverageMetricsRead(BaseModel):
    total_raw_quotes: int
    frozen_observations: int
    unique_player_matches: int
    unique_matches: int
    unique_market_lines: int
    bookmakers: list[str]
    market_families: list[str]
    average_snapshots_per_player_market: float | None


class MarketOpenTimingRead(BaseModel):
    player_id: int
    player_name: str
    match_id: int
    bookmaker_id: int
    bookmaker_name: str
    market_type: str
    line_type: str
    threshold: float
    first_observed_at: UtcDatetime
    first_hours_before_kickoff: float
    latest_observed_at: UtcDatetime
    latest_hours_before_kickoff: float
    n_price_changes: int
    n_observations: int


class RealMarketTrackingReportRead(BaseModel):
    label: str
    summary: DatasetSummaryRead
    model_vs_market: ModelVsMarketRead
    model_calibration: list[CalibrationBucketRead]
    market_calibration: list[CalibrationBucketRead]
    overall_return: HypotheticalReturnRead
    edge_buckets: list[BucketResultRead]
    confidence_buckets: list[BucketResultRead]
    lineup_buckets: list[BucketResultRead]
    timing_buckets: list[BucketResultRead]
    overall_sample_level: str
    coverage: CoverageMetricsRead
    market_open_timing: list[MarketOpenTimingRead]


class QuoteHistoryEntryRead(BaseModel):
    observed_at: UtcDatetime
    bookmaker_name: str
    offered_odds: float
    raw_implied_probability: float
    devigged_probability: float | None
    model_probability: float
    difference_pp: float
    confidence_tier: str
    selection_status_at_observation: str
    market_result: str | None


class NewPlayerCreate(BaseModel):
    display_name: str = Field(max_length=128)
    team_id: int
    note: str | None = Field(default=None, max_length=200)
    force: bool = False  # bypass the duplicate-display-name safety check


class NewPlayerRead(BaseModel):
    id: int
    display_name: str
    current_team_id: int | None
    source: str
    is_active: bool | None


class DuplicateNameWarningRead(BaseModel):
    duplicate_warning: bool = True
    existing_player_id: int
    existing_player_source: str
    existing_player_team_id: int | None


class PlayerAliasCreate(BaseModel):
    player_id: int
    alias_name: str = Field(max_length=128)
    source: str | None = Field(default=None, max_length=32)
    note: str | None = Field(default=None, max_length=200)


class PlayerAliasRead(BaseModel):
    id: int
    player_id: int
    player_name: str
    alias_name: str
    source: str | None
    note: str | None
    created_at: UtcDatetime


class MatchMarketDiagnosisRead(BaseModel):
    match_id: int
    category: str
    detail: str
    would_be_skipped_this_cycle: bool
    hours_to_kickoff: float
    disposals_available: bool
    goals_available: bool
    unique_player_count: int


class MatchCoverageStatusRead(BaseModel):
    match_id: int
    home_team_name: str
    away_team_name: str
    scheduled_start: UtcDatetime
    match_status: str
    simple_status: str
    lineup_announcement_state: str
    projections_generated: bool
    bookmaker_event_exists: bool
    bookmaker_props_observed: bool
    bookmakers_observed: list[str]
    n_quotes: int
    last_odds_refresh: UtcDatetime | None
    n_observations: int
    n_observations_settled: int
    n_observations_awaiting_settlement: int
    disposals_available: bool
    goals_available: bool
    unique_player_count: int
    diagnosis: MatchMarketDiagnosisRead


class RoundSummaryRead(BaseModel):
    n_upcoming_matches: int
    n_matches_with_projections: int
    n_matches_with_bookmaker_events: int
    n_matches_with_prop_markets: int
    n_unique_players_with_markets: int
    n_real_quotes_stored: int
    n_real_observations_stored: int
    n_confirmed_lineups: int
    n_placeholder_or_uncertain_lineups: int


class LiveCycleStepRead(BaseModel):
    step: str
    status: str
    detail: str


class LiveCycleRunRead(BaseModel):
    id: int
    run_at: UtcDatetime
    finished_at: UtcDatetime | None
    overall_status: str
    steps: list[LiveCycleStepRead]
    odds_credits_consumed: int | None
    odds_credits_remaining: int | None
    matches_affected: int
    quotes_added: int
    observations_added: int
    observations_settled: int
    team_odds_quotes_added: int
    weather_snapshots_added: int


class LiveStatusReportRead(BaseModel):
    round_summary: RoundSummaryRead
    matches: list[MatchCoverageStatusRead]
    recent_runs: list[LiveCycleRunRead]


class DataFreshnessItemRead(BaseModel):
    category: str
    label: str
    status: str  # fresh | aging | stale | not_available
    last_refreshed: UtcDatetime | None
    detail: str


class DataFreshnessReportRead(BaseModel):
    items: list[DataFreshnessItemRead]


class MarketMovementRead(BaseModel):
    player_id: int
    player_name: str
    match_id: int
    bookmaker_id: int
    bookmaker_name: str
    market_type: str
    line_type: str
    threshold: float
    first_odds: float
    latest_odds: float
    highest_odds: float
    lowest_odds: float
    first_difference_pp: float
    latest_difference_pp: float
    first_observed_at: UtcDatetime
    latest_observed_at: UtcDatetime
    n_observations: int
