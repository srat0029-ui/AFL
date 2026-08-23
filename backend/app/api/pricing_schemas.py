"""Response schemas for the B2B Pricing API (/api/v1/pricing/*).

Kept as its own module, separate from the main app.api.schemas — this is a
deliberately clean, independently versionable API surface meant for a
third-party consumer (another company's engineers), not the internal
product UI's schemas. See app/pricing/ for the underlying computation;
nothing here computes anything, it only shapes already-computed dataclass
output into a stable, typed, documented response.
"""

from pydantic import BaseModel

from app.api.schemas import UtcDatetime


class ModelProvenance(BaseModel):
    """Attached to every priced market (item 1's explicit requirement)."""

    model_name: str
    model_version: str
    generated_at: UtcDatetime
    data_cutoff: UtcDatetime


class ThresholdPriceRead(BaseModel):
    threshold: float
    line_type: str
    probability: float
    fair_odds: float


class LinePriceRead(BaseModel):
    line_value: float
    home_team: str
    away_team: str
    home_probability: float
    away_probability: float
    home_fair_odds: float
    away_fair_odds: float


class TotalPriceRead(BaseModel):
    line_value: float
    over_probability: float
    under_probability: float
    over_fair_odds: float
    under_fair_odds: float


class TeamMarketPriceRead(BaseModel):
    match_id: int
    home_team: str
    away_team: str
    provenance: ModelProvenance

    home_win_probability: float
    draw_probability: float
    away_win_probability: float
    home_fair_odds: float
    draw_fair_odds: float
    away_fair_odds: float

    expected_margin: float
    expected_total_points: float
    home_expected_score: float
    away_expected_score: float

    lines: list[LinePriceRead]
    totals: list[TotalPriceRead]


class CalibrationInfo(BaseModel):
    market_type: str
    requested_threshold: float
    evaluated_threshold: float
    ece: float
    n: int


class DisposalPriceRead(BaseModel):
    match_id: int
    player_id: int
    player_name: str
    team_id: int
    provenance: ModelProvenance
    lineup_status: str
    confidence_tier: str
    games_of_history: int

    expected: float
    distribution_method: str
    distribution_params: dict
    interval_50: tuple[float, float]
    interval_80: tuple[float, float]
    interval_90: tuple[float, float]
    thresholds: list[ThresholdPriceRead]
    calibration: CalibrationInfo | None
    warnings: list[str]
    is_stale: bool
    stale_reasons: list[str]


class GoalPriceRead(BaseModel):
    match_id: int
    player_id: int
    player_name: str
    team_id: int
    provenance: ModelProvenance
    lineup_status: str
    confidence_tier: str
    games_of_history: int

    expected: float
    distribution_kind: str
    distribution_params: dict
    scoring_archetype: str
    thresholds: list[ThresholdPriceRead]
    calibration: CalibrationInfo | None
    warnings: list[str]
    is_stale: bool
    stale_reasons: list[str]


class MatchPricingRead(BaseModel):
    match_id: int
    team: TeamMarketPriceRead
    disposals: list[DisposalPriceRead]
    goals: list[GoalPriceRead]


class RoundPricingRead(BaseModel):
    round_number: int | None
    season_year: int | None
    n_matches: int
    teams: list[TeamMarketPriceRead]
    disposals: list[DisposalPriceRead]
    goals: list[GoalPriceRead]


# --- Market Intelligence (separate layer, item 4) ---------------------------


class BookLineRead(BaseModel):
    bookmaker_name: str
    price_decimal: float
    recorded_at: UtcDatetime
    eligibility: str


class ConsensusRead(BaseModel):
    consensus_probability: float
    n_bookmakers: int
    n_devigged: int
    spread: float
    methodology: str


class OutlierRead(BaseModel):
    is_outlier: bool
    best_price: float
    median_eligible_price: float
    pct_difference: float
    message: str | None


class MarketIntelligenceRead(BaseModel):
    has_market: bool
    n_bookmakers: int
    best_price: float | None
    best_bookmaker: str | None
    consensus: ConsensusRead | None
    outlier: OutlierRead | None
    model_probability: float
    market_implied_probability: float | None
    difference_pp: float | None
    books: list[BookLineRead]


# --- Health / model-health ---------------------------------------------------


class HealthRead(BaseModel):
    status: str
    database: str


class ModelHealthEntry(BaseModel):
    model_name: str
    is_promoted: bool
    run_at: UtcDatetime | None
    detail: str


class ModelHealthRead(BaseModel):
    generated_at: UtcDatetime
    models: list[ModelHealthEntry]


# --- Integration health -------------------------------------------------------


class StaleWarningRead(BaseModel):
    category: str
    detail: str


class IntegrationHealthRead(BaseModel):
    status: str  # "ok" | "degraded"
    generated_at: UtcDatetime
    last_fixture_refresh: UtcDatetime | None
    last_odds_refresh: UtcDatetime | None
    current_round: int | None
    current_season_year: int | None
    promoted_models: dict[str, str]
    stale_warnings: list[StaleWarningRead]


# --- Model Registry + promotion audit trail ----------------------------------


class ModelRunSummaryRead(BaseModel):
    model_name: str
    model_version: str
    market: str
    status: str  # champion | previous_champion | challenger | rejected
    run_at: UtcDatetime
    tune_start_year: int
    tune_end_year: int
    evaluation_start_year: int
    evaluation_end_year: int
    sample_size: int | None
    point_metrics: dict
    calibration_metrics: dict
    promotion_reason: str | None


class PromotionEventRead(BaseModel):
    market: str
    previous_champion_model_name: str | None
    previous_champion_model_version: str | None
    new_champion_model_name: str
    new_champion_model_version: str
    promoted_at: UtcDatetime
    evidence_summary: str
    evaluation_metrics: dict


class DisposalHeadToHeadRead(BaseModel):
    ridge: ModelRunSummaryRead | None
    huber: ModelRunSummaryRead | None
    ridge_high_volume_bias: dict
    huber_high_volume_bias: dict
    ridge_low_history_bias: dict
    huber_low_history_bias: dict


class ModelRegistryRead(BaseModel):
    dataset_label: str = "Historical backtest"  # every run here is a backtest metric — never a live/prospective one
    disposal_models: list[ModelRunSummaryRead]
    goal_models: list[ModelRunSummaryRead]
    team_models: list[ModelRunSummaryRead]
    disposal_head_to_head: DisposalHeadToHeadRead
    promotion_events: list[PromotionEventRead]


# --- Prospective Live Evaluation ---------------------------------------------


class ProspectiveSplitRead(BaseModel):
    label: str
    n_settled: int
    n_unique_events: int
    model_brier: float | None
    market_brier: float | None
    model_log_loss: float | None
    market_log_loss: float | None
    model_calibration_ece: float | None
    n_with_market_consensus: int
    exploratory: bool


class ProspectiveEvaluationRead(BaseModel):
    dataset_label: str = "Prospective live evaluation"  # never a backtest number — see prospective_evaluation.py
    has_settled_data: bool
    n_frozen_total: int
    n_settled: int
    n_unique_player_match_events: int
    overall: ProspectiveSplitRead | None
    by_market_family: list[ProspectiveSplitRead]
    by_probability_bucket: list[ProspectiveSplitRead]
    by_model_version: list[ProspectiveSplitRead]
    message: str
