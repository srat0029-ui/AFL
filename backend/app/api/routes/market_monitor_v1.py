"""B2B Market Anomaly / Trading QA API (item 6) — read-only, versioned,
typed for a downstream engineering system. Reuses app.market_monitor.detector
directly; nothing here computes an anomaly itself."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.market_monitor_schemas import (
    AnomalyAlertRead,
    AnomalyListRead,
    AnomalySummaryRead,
    AnomalyTypeCount,
    BookmakerPriceRead,
    MatchAnomaliesRead,
    ModelRiskFlagRead,
    SeverityCount,
)
from app.market_monitor.detector import active_match_ids, detect_match_anomalies
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
