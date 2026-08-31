"""Trading Monitor / Pricing QA API (/api/v1/trading-monitor/*) — a
composition layer, not a second detection engine. Reads
app.market_monitor's own already-persisted/scored cases, plus the new
model-movement/SGM-monitoring/data-health modules in app.trading_monitor.
See app/trading_monitor/__init__.py for the full "what's reused vs new"
rationale.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.trading_monitor_schemas import (
    DataHealthFindingRead,
    DataHealthRead,
    FreshnessItemRead,
    LiveCycleHealthRead,
    ModelMovementRead,
    NeedsAttentionEntryRead,
    RecentActivityEntryRead,
    SettlementBacklogRead,
    SgmCoefficientProvenanceRead,
    SgmDivergenceEntryRead,
    SgmHorizonMovementRead,
    SgmMonitoringRead,
    TopSummaryRead,
    TradingMonitorOverviewRead,
)
from app.database import get_db
from app.trading_monitor.data_health import DataHealthReport, load_data_health
from app.trading_monitor.overview import TOP_N_DEFAULT, TradingMonitorOverview, load_trading_monitor_overview
from app.trading_monitor.sgm_monitor import SgmMonitoringReport, load_sgm_monitoring

router = APIRouter(prefix="/api/v1/trading-monitor", tags=["trading-monitor-v1"])


def _data_health_read(h: DataHealthReport) -> DataHealthRead:
    return DataHealthRead(
        freshness=[
            FreshnessItemRead(category=i.category, label=i.label, status=i.status, last_refreshed=i.last_refreshed, detail=i.detail)
            for i in h.freshness.items
        ],
        findings=[DataHealthFindingRead(category=f.category, label=f.label, severity=f.severity, detail=f.detail) for f in h.findings],
        backlog=SettlementBacklogRead(
            prop_observations_unsettled=h.backlog.prop_observations_unsettled,
            pricing_snapshots_unsettled=h.backlog.pricing_snapshots_unsettled,
            sgm_snapshots_unsettled=h.backlog.sgm_snapshots_unsettled,
        ),
        live_cycle=LiveCycleHealthRead(
            last_run_at=h.live_cycle.last_run_at, last_run_status=h.live_cycle.last_run_status,
            n_runs_checked=h.live_cycle.n_runs_checked, n_runs_with_failures=h.live_cycle.n_runs_with_failures,
            recent_failed_steps=h.live_cycle.recent_failed_steps,
        ),
    )


def _sgm_monitoring_read(s: SgmMonitoringReport) -> SgmMonitoringRead:
    def _entry(e):
        return SgmDivergenceEntryRead(
            match_id=e.match_id, leg_signature=e.leg_signature, n_legs=e.n_legs, model_probability=e.model_probability,
            naive_independence_probability=e.naive_independence_probability, naive_vs_joint_difference_pp=e.naive_vs_joint_difference_pp,
            correlation_adjustment_pp=e.correlation_adjustment_pp, correlation_adjustment_bucket=e.correlation_adjustment_bucket,
            snapshot_horizon=e.snapshot_horizon, generated_at=e.generated_at,
        )

    return SgmMonitoringRead(
        n_recent_snapshots=s.n_recent_snapshots,
        largest_naive_vs_joint_differences=[_entry(e) for e in s.largest_naive_vs_joint_differences],
        largest_correlation_adjustments=[_entry(e) for e in s.largest_correlation_adjustments],
        horizon_movements=[
            SgmHorizonMovementRead(
                match_id=m.match_id, leg_signature=m.leg_signature, n_legs=m.n_legs, earliest_horizon=m.earliest_horizon,
                earliest_probability=m.earliest_probability, latest_horizon=m.latest_horizon, latest_probability=m.latest_probability,
                absolute_change=m.absolute_change, is_beyond_mc_noise=m.is_beyond_mc_noise,
            )
            for m in s.horizon_movements
        ],
        coefficient_provenance=[
            SgmCoefficientProvenanceRead(
                market=c.market, slope=c.slope, intercept=c.intercept, n_observations=c.n_observations,
                model_version=c.model_version, fitted_at=c.fitted_at,
            )
            for c in s.coefficient_provenance
        ],
    )


def _needs_attention_read(e) -> NeedsAttentionEntryRead:
    return NeedsAttentionEntryRead(
        case_id=e.case_id, match_id=e.match_id, home_team=e.home_team, away_team=e.away_team, player_name=e.player_name,
        market_type=e.market_type, selection=e.selection, threshold=e.threshold, tier=e.tier, total_score=e.total_score,
        primary_alert_type=e.primary_alert_type, severity=e.severity, detail=e.detail, lifecycle=e.lifecycle,
    )


def _model_movement_read(m) -> ModelMovementRead:
    return ModelMovementRead(
        match_id=m.match_id, player_id=m.player_id, value_type=m.value_type, value_kind=m.value_kind, selection=m.selection,
        threshold=m.threshold, previous_value=m.previous_value, current_value=m.current_value, absolute_change=m.absolute_change,
        relative_change=m.relative_change, hours_between=m.hours_between, previous_recorded_at=m.previous_recorded_at,
        recorded_at=m.recorded_at, model_name=m.model_name, model_version=m.model_version,
        lineup_status_changed=m.lineup_status_changed, previous_lineup_status=m.previous_lineup_status,
        current_lineup_status=m.current_lineup_status, is_notable=m.is_notable, is_material=m.is_material,
    )


def _overview_read(o: TradingMonitorOverview) -> TradingMonitorOverviewRead:
    return TradingMonitorOverviewRead(
        generated_at=o.generated_at,
        summary=TopSummaryRead(
            n_upcoming_matches=o.summary.n_upcoming_matches, n_fresh_markets=o.summary.n_fresh_markets,
            n_stale_or_warning_findings=o.summary.n_stale_or_warning_findings,
            n_active_error_or_warning=o.summary.n_active_error_or_warning,
            n_material_model_movements=o.summary.n_material_model_movements, n_market_movement_cases=o.summary.n_market_movement_cases,
        ),
        needs_attention=[_needs_attention_read(e) for e in o.needs_attention],
        market_movers=[_needs_attention_read(e) for e in o.market_movers],
        dispersion=[_needs_attention_read(e) for e in o.dispersion],
        model_movers=[_model_movement_read(m) for m in o.model_movers],
        data_health=_data_health_read(o.data_health),
        sgm=_sgm_monitoring_read(o.sgm),
        recent_activity=[
            RecentActivityEntryRead(run_at=r.run_at, overall_status=r.overall_status, n_steps_failed=r.n_steps_failed)
            for r in o.recent_activity
        ],
    )


@router.get("/overview", response_model=TradingMonitorOverviewRead)
def get_trading_monitor_overview(limit: int = TOP_N_DEFAULT, db: Session = Depends(get_db)) -> TradingMonitorOverviewRead:
    limit = max(1, min(limit, 100))
    return _overview_read(load_trading_monitor_overview(db, limit=limit))


@router.get("/data-health", response_model=DataHealthRead)
def get_trading_monitor_data_health(db: Session = Depends(get_db)) -> DataHealthRead:
    return _data_health_read(load_data_health(db))


@router.get("/sgm", response_model=SgmMonitoringRead)
def get_trading_monitor_sgm(limit: int = TOP_N_DEFAULT, db: Session = Depends(get_db)) -> SgmMonitoringRead:
    limit = max(1, min(limit, 100))
    return _sgm_monitoring_read(load_sgm_monitoring(db, limit=limit))
