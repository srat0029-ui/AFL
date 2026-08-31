"""Response schemas for the Trading Monitor API (/api/v1/trading-monitor/*)
— a composition layer over already-existing systems (app.market_monitor,
app.trading_monitor), same separately-versioned-schema-file convention as
pricing_schemas.py/market_monitor_schemas.py."""

from pydantic import BaseModel

from app.api.schemas import UtcDatetime


class NeedsAttentionEntryRead(BaseModel):
    case_id: str
    match_id: int
    home_team: str
    away_team: str
    player_name: str | None
    market_type: str
    selection: str | None
    threshold: float | None
    tier: str
    total_score: float
    primary_alert_type: str
    severity: str
    detail: str
    lifecycle: str


class ModelMovementRead(BaseModel):
    match_id: int
    player_id: int | None
    value_type: str
    value_kind: str
    selection: str | None
    threshold: float | None
    previous_value: float
    current_value: float
    absolute_change: float
    relative_change: float | None
    hours_between: float
    previous_recorded_at: UtcDatetime
    recorded_at: UtcDatetime
    model_name: str
    model_version: str
    lineup_status_changed: bool
    previous_lineup_status: str | None
    current_lineup_status: str | None
    is_notable: bool
    is_material: bool


class FreshnessItemRead(BaseModel):
    category: str
    label: str
    status: str
    last_refreshed: UtcDatetime | None
    detail: str


class DataHealthFindingRead(BaseModel):
    category: str
    label: str
    severity: str
    detail: str


class SettlementBacklogRead(BaseModel):
    prop_observations_unsettled: int
    pricing_snapshots_unsettled: int
    sgm_snapshots_unsettled: int


class LiveCycleHealthRead(BaseModel):
    last_run_at: UtcDatetime | None
    last_run_status: str | None
    n_runs_checked: int
    n_runs_with_failures: int
    recent_failed_steps: list[str]


class DataHealthRead(BaseModel):
    freshness: list[FreshnessItemRead]
    findings: list[DataHealthFindingRead]
    backlog: SettlementBacklogRead
    live_cycle: LiveCycleHealthRead


class SgmDivergenceEntryRead(BaseModel):
    match_id: int
    leg_signature: str
    n_legs: int
    model_probability: float
    naive_independence_probability: float
    naive_vs_joint_difference_pp: float
    correlation_adjustment_pp: float
    correlation_adjustment_bucket: str
    snapshot_horizon: str
    generated_at: UtcDatetime


class SgmHorizonMovementRead(BaseModel):
    match_id: int
    leg_signature: str
    n_legs: int
    earliest_horizon: str
    earliest_probability: float
    latest_horizon: str
    latest_probability: float
    absolute_change: float
    is_beyond_mc_noise: bool


class SgmCoefficientProvenanceRead(BaseModel):
    market: str
    slope: float
    intercept: float
    n_observations: int
    model_version: str
    fitted_at: UtcDatetime


class SgmMonitoringRead(BaseModel):
    n_recent_snapshots: int
    largest_naive_vs_joint_differences: list[SgmDivergenceEntryRead]
    largest_correlation_adjustments: list[SgmDivergenceEntryRead]
    horizon_movements: list[SgmHorizonMovementRead]
    coefficient_provenance: list[SgmCoefficientProvenanceRead]


class RecentActivityEntryRead(BaseModel):
    run_at: UtcDatetime
    overall_status: str
    n_steps_failed: int


class TopSummaryRead(BaseModel):
    n_upcoming_matches: int
    n_fresh_markets: int
    n_stale_or_warning_findings: int
    n_active_error_or_warning: int
    n_material_model_movements: int
    n_market_movement_cases: int


class TradingMonitorOverviewRead(BaseModel):
    generated_at: UtcDatetime
    summary: TopSummaryRead
    needs_attention: list[NeedsAttentionEntryRead]
    market_movers: list[NeedsAttentionEntryRead]
    dispersion: list[NeedsAttentionEntryRead]
    model_movers: list[ModelMovementRead]
    data_health: DataHealthRead
    sgm: SgmMonitoringRead
    recent_activity: list[RecentActivityEntryRead]
