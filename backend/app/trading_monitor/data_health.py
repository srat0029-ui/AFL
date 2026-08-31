"""Data-quality / freshness operational checks — composes ALREADY-EXISTING
freshness/health signals (`app.player_modelling.data_freshness.
load_data_freshness`, `LiveCycleRun` history) plus a handful of simple
settlement-backlog counts. No new detection logic and no new persistence:
every one of these is either already computed elsewhere or a plain
`count(*) WHERE outcome IS NULL` over a table this project already has.

Severity classification follows the brief's exact three-way split:
  ERROR   — a system/data failure (a live-cycle step actually failed).
  WARNING — reduced confidence / incomplete data (stale odds, uncertain
            lineups) — not broken, just not fully trustworthy right now.
  INFO    — an expected state (markets not open yet three weeks out) or a
            notable-but-normal condition. Never inflated to WARNING/ERROR
            just to make the page look active — matches this project's own
            "not_available is not the same claim as broken" convention
            already used by `data_freshness.py`.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    LiveCycleRun,
    PricingSnapshot,
    PropMarketObservation,
    RUN_OK,
    SgmPriceSnapshot,
    STEP_BLOCKING_FAILURE,
    STEP_RECOVERABLE_FAILURE,
)
from app.player_modelling.data_freshness import AGING, FRESH, NOT_AVAILABLE, STALE, DataFreshnessReport, load_data_freshness
from app.trading_monitor.thresholds import STALE_LIVE_CYCLE_RUNS_TO_CHECK

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

_FRESHNESS_SEVERITY = {FRESH: SEVERITY_INFO, AGING: SEVERITY_INFO, STALE: SEVERITY_WARNING, NOT_AVAILABLE: SEVERITY_INFO}


@dataclass(frozen=True)
class DataHealthFinding:
    category: str
    label: str
    severity: str
    detail: str


@dataclass(frozen=True)
class SettlementBacklog:
    prop_observations_unsettled: int
    pricing_snapshots_unsettled: int
    sgm_snapshots_unsettled: int


@dataclass(frozen=True)
class LiveCycleHealth:
    last_run_at: datetime | None
    last_run_status: str | None
    n_runs_checked: int
    n_runs_with_failures: int
    recent_failed_steps: list[str]  # deduplicated step names that failed in any of the recent runs checked


@dataclass(frozen=True)
class DataHealthReport:
    freshness: DataFreshnessReport
    findings: list[DataHealthFinding]
    backlog: SettlementBacklog
    live_cycle: LiveCycleHealth


def _freshness_findings(freshness: DataFreshnessReport) -> list[DataHealthFinding]:
    return [
        DataHealthFinding(category=item.category, label=item.label, severity=_FRESHNESS_SEVERITY[item.status], detail=item.detail)
        for item in freshness.items
    ]


def _settlement_backlog(db: Session) -> SettlementBacklog:
    return SettlementBacklog(
        prop_observations_unsettled=db.scalar(select(func.count()).select_from(PropMarketObservation).where(PropMarketObservation.market_result.is_(None))) or 0,
        pricing_snapshots_unsettled=db.scalar(select(func.count()).select_from(PricingSnapshot).where(PricingSnapshot.outcome.is_(None))) or 0,
        sgm_snapshots_unsettled=db.scalar(select(func.count()).select_from(SgmPriceSnapshot).where(SgmPriceSnapshot.outcome.is_(None))) or 0,
    )


def _live_cycle_health(recent: list[LiveCycleRun]) -> LiveCycleHealth:
    if not recent:
        return LiveCycleHealth(last_run_at=None, last_run_status=None, n_runs_checked=0, n_runs_with_failures=0, recent_failed_steps=[])

    n_with_failures = sum(1 for r in recent if r.overall_status != RUN_OK)
    failed_steps: list[str] = []
    for run in recent:
        for step in run.steps:
            if step.get("status") in (STEP_RECOVERABLE_FAILURE, STEP_BLOCKING_FAILURE) and step.get("step") not in failed_steps:
                failed_steps.append(step["step"])
    return LiveCycleHealth(
        last_run_at=recent[0].run_at, last_run_status=recent[0].overall_status, n_runs_checked=len(recent),
        n_runs_with_failures=n_with_failures, recent_failed_steps=failed_steps,
    )


def _worst_step_status(runs: list[LiveCycleRun], step_name: str) -> str:
    """A BLOCKING_FAILURE anywhere in the recent window outranks a merely
    RECOVERABLE_FAILURE for the same step name - severity should reflect
    the worst thing that actually happened, not just the most recent run."""
    worst = STEP_RECOVERABLE_FAILURE
    for run in runs:
        for step in run.steps:
            if step.get("step") == step_name and step.get("status") == STEP_BLOCKING_FAILURE:
                worst = STEP_BLOCKING_FAILURE
    return worst


def load_data_health(db: Session) -> DataHealthReport:
    freshness = load_data_freshness(db)
    findings = _freshness_findings(freshness)

    recent_runs = db.scalars(select(LiveCycleRun).order_by(LiveCycleRun.run_at.desc()).limit(STALE_LIVE_CYCLE_RUNS_TO_CHECK)).all()
    live_cycle = _live_cycle_health(recent_runs)
    for step in live_cycle.recent_failed_steps:
        # Severity reflects the step's OWN worst outcome, not the overall
        # run status - a single recoverable failure inside an otherwise-
        # partial run is still just a WARNING, never inflated to ERROR.
        severity = SEVERITY_ERROR if _worst_step_status(recent_runs, step) == STEP_BLOCKING_FAILURE else SEVERITY_WARNING
        findings.append(DataHealthFinding(
            category="live_cycle", label=f"Live cycle step: {step}", severity=severity,
            detail=f"Failed in at least one of the last {live_cycle.n_runs_checked} run(s).",
        ))

    return DataHealthReport(freshness=freshness, findings=findings, backlog=_settlement_backlog(db), live_cycle=live_cycle)
