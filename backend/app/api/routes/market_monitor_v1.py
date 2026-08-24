"""B2B Market Anomaly / Trading QA API (item 6) — read-only, versioned,
typed for a downstream engineering system. Reuses app.market_monitor.detector
directly; nothing here computes an anomaly itself."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.market_monitor_schemas import (
    AlertTypeEffectivenessRead,
    AnomalyAlertRead,
    AnomalyCaseRead,
    AnomalyListRead,
    AnomalySummaryRead,
    AnomalyTypeCount,
    BookmakerPriceRead,
    EffectivenessDashboardRead,
    EffectivenessSummaryRead,
    EffectivenessViewRead,
    MatchAnomaliesRead,
    ModelRiskFlagRead,
    PriorityComponentRead,
    ProspectiveCoverageRead,
    ResearchCategorySummaryRead,
    SetCaseStatusRequest,
    SeverityCount,
    TierCount,
    TraderInboxRead,
)
from app.market_monitor.case_persistence import set_manual_status
from app.market_monitor.detector import active_match_ids, detect_match_anomalies
from app.market_monitor.effectiveness import compute_alert_type_effectiveness, compute_effectiveness_summary, compute_research_category_summary
from app.market_monitor.inbox import RankedCase, build_trader_inbox
from app.market_monitor.prospective_coverage import compute_prospective_coverage
from app.market_monitor.types import Alert
from app.models import Match

router = APIRouter(prefix="/api/v1/market-monitor", tags=["market-monitor-v1"])


def _alert_read(a: Alert) -> AnomalyAlertRead:
    return AnomalyAlertRead(
        alert_type=a.alert_type, severity=a.severity, reason_code=a.reason_code, detail=a.detail,
        match_id=a.match_id, home_team=a.home_team, away_team=a.away_team, player_id=a.player_id,
        player_name=a.player_name, team_id=a.team_id, market_type=a.market_type, selection=a.selection,
        threshold=a.threshold, line_value=a.line_value, model_probability=a.model_probability,
        model_fair_odds=a.model_fair_odds, market_consensus_probability=a.market_consensus_probability,
        bookmaker_prices=[BookmakerPriceRead(bookmaker_name=b.bookmaker_name, price_decimal=b.price_decimal, recorded_at=b.recorded_at, eligibility=b.eligibility) for b in a.bookmaker_prices],
        freshness=a.freshness, model_version=a.model_version, lineup_status=a.lineup_status, context_state=a.context_state,
        model_risk_flags=[ModelRiskFlagRead(code=f.code, description=f.description) for f in a.model_risk_flags],
        generated_at=a.generated_at,
    )


def _scan_active(db: Session) -> tuple[list[Alert], int]:
    match_ids = active_match_ids(db)
    alerts: list[Alert] = []
    for mid in match_ids:
        alerts += detect_match_anomalies(db, mid)
    return alerts, len(match_ids)


@router.get("/anomalies", response_model=AnomalyListRead)
def get_anomalies(
    alert_type: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    match_id: int | None = Query(default=None),
    bookmaker_name: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> AnomalyListRead:
    alerts, n_matches = _scan_active(db)
    if alert_type:
        alerts = [a for a in alerts if a.alert_type == alert_type]
    if severity:
        alerts = [a for a in alerts if a.severity == severity]
    if match_id is not None:
        alerts = [a for a in alerts if a.match_id == match_id]
    if bookmaker_name:
        alerts = [a for a in alerts if any(b.bookmaker_name == bookmaker_name for b in a.bookmaker_prices)]
    from datetime import datetime, timezone

    return AnomalyListRead(
        generated_at=datetime.now(timezone.utc), n_matches_scanned=n_matches, total=len(alerts),
        alerts=[_alert_read(a) for a in alerts[:limit]],
    )


@router.get("/matches/{match_id}", response_model=MatchAnomaliesRead)
def get_match_anomalies(match_id: int, db: Session = Depends(get_db)) -> MatchAnomaliesRead:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="match not found")
    alerts = detect_match_anomalies(db, match_id)
    home = alerts[0].home_team if alerts else match.home_team.name
    away = alerts[0].away_team if alerts else match.away_team.name
    return MatchAnomaliesRead(match_id=match_id, home_team=home, away_team=away, alerts=[_alert_read(a) for a in alerts])


@router.get("/summary", response_model=AnomalySummaryRead)
def get_summary(db: Session = Depends(get_db)) -> AnomalySummaryRead:
    alerts, n_matches = _scan_active(db)
    from collections import Counter
    from datetime import datetime, timezone

    by_type = Counter(a.alert_type for a in alerts)
    by_severity = Counter(a.severity for a in alerts)
    return AnomalySummaryRead(
        generated_at=datetime.now(timezone.utc), n_matches_scanned=n_matches, total_anomalies=len(alerts),
        by_type=[AnomalyTypeCount(alert_type=t, count=c) for t, c in sorted(by_type.items(), key=lambda kv: -kv[1])],
        by_severity=[SeverityCount(severity=s, count=c) for s, c in sorted(by_severity.items(), key=lambda kv: -kv[1])],
    )


def _case_read(r: RankedCase) -> AnomalyCaseRead:
    c = r.case
    return AnomalyCaseRead(
        case_id=c.case_id, match_id=c.match_id, home_team=c.home_team, away_team=c.away_team, player_id=c.player_id,
        player_name=c.player_name, team_id=c.team_id, market_type=c.market_type, selection=c.selection,
        threshold=c.threshold, line_value=c.line_value, primary_alert=_alert_read(c.primary_alert),
        supporting_alert_types=c.supporting_alert_types, alerts=[_alert_read(a) for a in c.alerts], bookmakers=c.bookmakers,
        first_detected=c.first_detected, latest_detected=c.latest_detected, priority_score=r.priority.total_score,
        tier=r.priority.tier,
        components=[PriorityComponentRead(name=comp.name, raw_value=comp.raw_value, normalized=comp.normalized, weight=comp.weight, contribution=comp.contribution, explanation=comp.explanation) for comp in r.priority.components],
        persistence_label=r.priority.persistence_label, n_snapshots=r.priority.n_snapshots, model_support=r.priority.model_support,
        lifecycle=r.lifecycle, manual_status=r.manual_status,
    )


@router.get("/cases", response_model=TraderInboxRead)
def get_cases(
    tier: str | None = Query(default=None),
    alert_type: str | None = Query(default=None),
    match_id: int | None = Query(default=None),
    bookmaker_name: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=500),
    db: Session = Depends(get_db),
) -> TraderInboxRead:
    """item 9's default data source — ranked, deduplicated cases, NOT raw
    alerts (see /anomalies for the raw feed). Defaults to the top 20 by
    priority score; filters narrow the same ranked list, never a separate
    query path."""
    from collections import Counter
    from datetime import datetime, timezone

    match_ids = active_match_ids(db)
    ranked = build_trader_inbox(db, match_ids, track_persistence=True, full_scan=True)
    total_raw = sum(len(r.case.alerts) for r in ranked)

    filtered = ranked
    if tier:
        filtered = [r for r in filtered if r.priority.tier == tier]
    if alert_type:
        filtered = [r for r in filtered if r.case.primary_alert.alert_type == alert_type or alert_type in r.case.supporting_alert_types]
    if match_id is not None:
        filtered = [r for r in filtered if r.case.match_id == match_id]
    if bookmaker_name:
        filtered = [r for r in filtered if bookmaker_name in r.case.bookmakers]

    tier_counts = Counter(r.priority.tier for r in ranked)
    return TraderInboxRead(
        generated_at=datetime.now(timezone.utc), n_matches_scanned=len(match_ids), total_raw_alerts=total_raw,
        total_cases=len(ranked), tier_counts=[TierCount(tier=t, count=c) for t, c in sorted(tier_counts.items(), key=lambda kv: -kv[1])],
        cases=[_case_read(r) for r in filtered[:limit]],
    )


@router.patch("/cases/{case_id}/status")
def patch_case_status(case_id: str, body: SetCaseStatusRequest, db: Session = Depends(get_db)) -> dict:
    """Read-only/system statuses (new/persisting/resolved_naturally) are
    never set here — only the manual field (item 10's explicit allowance:
    "do not require authentication/workflow permissions yet")."""
    record = set_manual_status(db, case_id, body.status)
    if record is None:
        raise HTTPException(status_code=404, detail="no tracked case with this case_id yet — call GET /cases first so it's been observed at least once")
    db.commit()
    return {"case_id": case_id, "manual_status": record.manual_status}


@router.get("/effectiveness", response_model=EffectivenessDashboardRead)
def get_effectiveness(db: Session = Depends(get_db)) -> EffectivenessDashboardRead:
    """Prospective Alert Validation dashboard (items 7-8, extended by the
    Genuine Prospective Operation stage's items 4-5): read-only, purely
    descriptive aggregation over already-settled AnomalyCaseSnapshot rows —
    never a live re-detection, never touches a threshold/weight/probability.
    `prospective` and `retrospective` are always kept as separate views
    (item 5) — never blended into one number. Always reports sample size
    alongside every rate; a denominator below effectiveness.EARLY_EVIDENCE_MIN_N
    carries sample_label="Early evidence" instead of being presented as stable."""
    from datetime import datetime, timezone

    def _view(capture_mode: str) -> EffectivenessViewRead:
        summary = compute_effectiveness_summary(db, capture_mode=capture_mode)
        by_type = compute_alert_type_effectiveness(db, capture_mode=capture_mode)
        return EffectivenessViewRead(
            summary=EffectivenessSummaryRead(**summary.__dict__),
            by_alert_type=[AlertTypeEffectivenessRead(**a.__dict__) for a in by_type],
        )

    return EffectivenessDashboardRead(
        generated_at=datetime.now(timezone.utc),
        coverage=ProspectiveCoverageRead(**compute_prospective_coverage(db).__dict__),
        prospective=_view("prospective"),
        retrospective=_view("retrospective"),
        research_category=ResearchCategorySummaryRead(**compute_research_category_summary(db, capture_mode="prospective").__dict__),
    )
