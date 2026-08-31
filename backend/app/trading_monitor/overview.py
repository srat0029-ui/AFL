"""The Trading Monitor's single composition point — pulls together
already-existing, already-tested systems rather than re-deriving anything:

- "Needs attention" / "Market movers": `app.market_monitor.inbox.
  build_trader_inbox` (unchanged, reused as-is — this package never
  touches market_monitor's own detection/scoring/persistence).
- "Model movers": the one genuinely new signal this phase adds
  (`app.player_modelling.model_movement`).
- "Bookmaker dispersion": the `LARGE_MARKET_DISPERSION`-type cases already
  surfaced by the trader inbox.
- "Data health": `app.trading_monitor.data_health` (itself a thin
  composition over existing freshness/settlement/live-cycle data).
- "SGM": `app.trading_monitor.sgm_monitor` (reads existing SgmPriceSnapshot
  history, no new persistence).
- "Recent activity": the same `LiveCycleRun` rows `LiveStatusPage` already
  reads.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.market_monitor.detector import active_match_ids
from app.market_monitor.inbox import RankedCase, build_trader_inbox
from app.market_monitor.types import (
    BOOKMAKER_MOVED_VS_STABLE_CONSENSUS,
    CONSENSUS_MOVED_VS_STALE_BOOKMAKER,
    LARGE_MARKET_DISPERSION,
    SHARP_MARKET_MOVE_MODEL_STABLE,
)
from app.models import LiveCycleRun
from app.player_modelling.model_movement import ModelMovement, recent_model_movements
from app.player_modelling.upcoming_features import load_next_upcoming_round
from app.trading_monitor.data_health import DataHealthReport, load_data_health
from app.trading_monitor.sgm_monitor import SgmMonitoringReport, load_sgm_monitoring

TOP_N_DEFAULT = 20
NEEDS_ATTENTION_TIERS = ("critical", "high_priority")
_MOVEMENT_ALERT_TYPES = (SHARP_MARKET_MOVE_MODEL_STABLE, BOOKMAKER_MOVED_VS_STABLE_CONSENSUS, CONSENSUS_MOVED_VS_STALE_BOOKMAKER)


@dataclass(frozen=True)
class NeedsAttentionEntry:
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


@dataclass(frozen=True)
class TopSummary:
    n_upcoming_matches: int
    n_fresh_markets: int
    n_stale_or_warning_findings: int
    n_active_error_or_warning: int
    n_material_model_movements: int
    n_market_movement_cases: int


@dataclass(frozen=True)
class RecentActivityEntry:
    run_at: datetime
    overall_status: str
    n_steps_failed: int


@dataclass(frozen=True)
class TradingMonitorOverview:
    generated_at: datetime
    summary: TopSummary
    needs_attention: list[NeedsAttentionEntry]
    market_movers: list[NeedsAttentionEntry]
    dispersion: list[NeedsAttentionEntry]
    model_movers: list[ModelMovement]
    data_health: DataHealthReport
    sgm: SgmMonitoringReport
    recent_activity: list[RecentActivityEntry]


def _needs_attention_entry(r: RankedCase) -> NeedsAttentionEntry:
    c = r.case
    return NeedsAttentionEntry(
        case_id=c.case_id, match_id=c.match_id, home_team=c.home_team, away_team=c.away_team, player_name=c.player_name,
        market_type=c.market_type, selection=c.selection, threshold=c.threshold, tier=r.priority.tier,
        total_score=r.priority.total_score, primary_alert_type=c.primary_alert.alert_type, severity=c.primary_alert.severity,
        detail=c.primary_alert.detail, lifecycle=r.lifecycle,
    )


def _has_alert_type(case: RankedCase, alert_types: tuple[str, ...]) -> bool:
    all_types = {case.case.primary_alert.alert_type, *case.case.supporting_alert_types}
    return bool(all_types & set(alert_types))


def load_trading_monitor_overview(db: Session, *, limit: int = TOP_N_DEFAULT) -> TradingMonitorOverview:
    now = datetime.now(timezone.utc)
    upcoming = load_next_upcoming_round(db)
    match_ids = active_match_ids(db)

    # Same call shape as market_monitor_v1.py's own /cases endpoint - reads
    # here also update case persistence bookkeeping (idempotent, matching
    # every other caller of build_trader_inbox), so this page's lifecycle/
    # manual_status never disagrees with Market Monitor's own page.
    ranked_cases = build_trader_inbox(db, match_ids, now=now, track_persistence=True, full_scan=True)

    needs_attention = [_needs_attention_entry(r) for r in ranked_cases if r.priority.tier in NEEDS_ATTENTION_TIERS][:limit]
    market_movers = [_needs_attention_entry(r) for r in ranked_cases if _has_alert_type(r, _MOVEMENT_ALERT_TYPES)][:limit]
    dispersion = [_needs_attention_entry(r) for r in ranked_cases if _has_alert_type(r, (LARGE_MARKET_DISPERSION,))][:limit]

    model_movers = recent_model_movements(db, [m.match_id for m in upcoming])
    material_model_movers = [m for m in model_movers if m.is_material]

    health = load_data_health(db)
    sgm = load_sgm_monitoring(db, limit=limit)

    recent_runs = db.scalars(select(LiveCycleRun).order_by(LiveCycleRun.run_at.desc()).limit(limit)).all()
    recent_activity = [
        RecentActivityEntry(
            run_at=run.run_at, overall_status=run.overall_status,
            n_steps_failed=sum(1 for s in run.steps if s.get("status") in ("recoverable_failure", "blocking_failure")),
        )
        for run in recent_runs
    ]

    summary = TopSummary(
        n_upcoming_matches=len(upcoming),
        n_fresh_markets=sum(1 for item in health.freshness.items if item.status == "fresh"),
        n_stale_or_warning_findings=sum(1 for f in health.findings if f.severity in ("warning", "error")),
        n_active_error_or_warning=len(needs_attention) + sum(1 for f in health.findings if f.severity in ("warning", "error")),
        n_material_model_movements=len(material_model_movers),
        n_market_movement_cases=len(market_movers),
    )

    return TradingMonitorOverview(
        generated_at=now, summary=summary, needs_attention=needs_attention, market_movers=market_movers,
        dispersion=dispersion, model_movers=model_movers[:limit], data_health=health, sgm=sgm, recent_activity=recent_activity,
    )
