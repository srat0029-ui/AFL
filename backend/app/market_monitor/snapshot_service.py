"""Freeze/evaluate anomaly alerts (item 8) — mirrors app/pricing/
snapshot_service.py's exact discipline: freezing is idempotent (re-running
before kickoff on an unchanged alert is a no-op, never a duplicate or a
rewrite), and settlement writes each evaluation field exactly once, never
touching an already-settled row again.

Settlement rules (all rule-based on already-recorded, already-validated
data — never uses information a real trading desk wouldn't have had at
match time, and never adjusts any of the detection thresholds in this
package based on what the outcomes look like — item 8's own boundary):
  - consensus_moved_toward_model: re-run the exact same market_intelligence
    lookup at evaluation time; the market moved toward the model if the
    gap |consensus - model| shrank versus what was frozen.
  - outlier_converged: was the same bookmaker still an outlier (re-run
    detect_outlier_bookmaker via a fresh MarketIntelligence lookup).
  - stale_market_repriced: does a bookmaker quote now exist that was
    recorded AFTER the frozen context event?
  - curve_anomaly_resolved: re-run the exact same curve check at
    evaluation time; resolved if it no longer fires.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AnomalyAlertSnapshot, Match, MatchStatus, OddsQuote, PlayerPropMarket
from app.market_monitor.common import aware
from app.market_monitor.curve_integrity import CurvePoint, check_monotonicity, find_adjacent_jumps
from app.market_monitor.detector import price_single_match
from app.market_monitor.types import (
    ADJACENT_THRESHOLD_JUMP,
    BOOKMAKER_VS_CONSENSUS_OUTLIER,
    MODEL_VS_MARKET_DIVERGENCE,
    NON_MONOTONIC_PLAYER_PRICE_CURVE,
    STALE_AFTER_CONTEXT_CHANGE,
    STALE_AFTER_LINEUP_CHANGE,
    Alert,
)
from app.pricing.market_intelligence import player_market_intelligence, team_market_intelligence


def freeze_alert(db: Session, alert: Alert) -> AnomalyAlertSnapshot | None:
    existing = db.scalar(
        select(AnomalyAlertSnapshot.id).where(
            AnomalyAlertSnapshot.match_id == alert.match_id, AnomalyAlertSnapshot.alert_type == alert.alert_type,
            AnomalyAlertSnapshot.market_type == alert.market_type, AnomalyAlertSnapshot.selection == alert.selection,
            AnomalyAlertSnapshot.threshold == alert.threshold, AnomalyAlertSnapshot.line_value == alert.line_value,
            AnomalyAlertSnapshot.reason_code == alert.reason_code,
        )
    )
    if existing is not None:
        return None  # already frozen at this identity — never overwritten, never duplicated

    row = AnomalyAlertSnapshot(
        match_id=alert.match_id, player_id=alert.player_id, team_id=alert.team_id, alert_type=alert.alert_type,
        severity=alert.severity, reason_code=alert.reason_code, detail=alert.detail, market_type=alert.market_type,
        selection=alert.selection, threshold=alert.threshold, line_value=alert.line_value,
        model_probability=alert.model_probability, model_fair_odds=alert.model_fair_odds,
        market_consensus_probability=alert.market_consensus_probability, model_version=alert.model_version,
        lineup_status=alert.lineup_status, context_state=alert.context_state, freshness=alert.freshness,
        frozen_at=alert.generated_at,
    )
    db.add(row)
    return row


def freeze_anomaly_alerts(db: Session, match_ids: list[int]) -> int:
    """Only for matches still SCHEDULED (item 8: "freeze before kickoff") —
    a completed match's alerts describe a market that no longer exists to
    monitor."""
    from app.market_monitor.detector import detect_match_anomalies

    n_frozen = 0
    for match_id in match_ids:
        match = db.get(Match, match_id)
        if match is None or match.status != MatchStatus.SCHEDULED:
            continue
        for alert in detect_match_anomalies(db, match_id):
            if freeze_alert(db, alert) is not None:
                n_frozen += 1
    db.commit()
    return n_frozen


def _settle_divergence_or_outlier(db: Session, snap: AnomalyAlertSnapshot) -> None:
    if snap.player_id is not None:
        intel = player_market_intelligence(db, snap.match_id, snap.player_id, snap.market_type, "over_under", snap.threshold, snap.model_probability or 0.0)
    else:
        intel = team_market_intelligence(db, snap.match_id, snap.market_type, snap.selection, snap.line_value, snap.model_probability or 0.0)
    if not intel.has_market:
        snap.evaluation_note = "No market available at evaluation time."
        return
    if snap.alert_type == MODEL_VS_MARKET_DIVERGENCE and snap.market_consensus_probability is not None and intel.market_implied_probability is not None:
        frozen_gap = abs(snap.market_consensus_probability - (snap.model_probability or 0.0))
        latest_gap = abs(intel.market_implied_probability - (snap.model_probability or 0.0))
        snap.consensus_moved_toward_model = latest_gap < frozen_gap
    if snap.alert_type == BOOKMAKER_VS_CONSENSUS_OUTLIER:
        snap.outlier_converged = intel.outlier is None or not intel.outlier.is_outlier


def _settle_staleness(db: Session, snap: AnomalyAlertSnapshot) -> None:
    quote_model = PlayerPropMarket if snap.player_id is not None else OddsQuote
    filters = [quote_model.match_id == snap.match_id]
    if snap.player_id is not None:
        filters += [quote_model.player_id == snap.player_id, quote_model.market_type == snap.market_type, quote_model.threshold == snap.threshold]
    else:
        filters += [quote_model.market_type == snap.market_type, quote_model.selection == snap.selection]
    latest = db.scalar(select(quote_model).where(*filters).order_by(quote_model.recorded_at.desc()))
    if latest is None:
        snap.stale_market_repriced = False
        return
    frozen_at = aware(snap.frozen_at)
    snap.stale_market_repriced = aware(latest.recorded_at) > frozen_at


def _settle_curve(db: Session, snap: AnomalyAlertSnapshot) -> None:
    team, disposals, goals = price_single_match(db, snap.match_id)
    price = next((p for p in (disposals + goals) if p.player_id == snap.player_id), None)
    if price is None:
        snap.evaluation_note = "No current projection for this player at evaluation time."
        return
    points = [CurvePoint(t.threshold, t.probability) for t in price.thresholds]
    if snap.alert_type == NON_MONOTONIC_PLAYER_PRICE_CURVE:
        snap.curve_anomaly_resolved = check_monotonicity(points).is_monotonic
    elif snap.alert_type == ADJACENT_THRESHOLD_JUMP:
        snap.curve_anomaly_resolved = len(find_adjacent_jumps(points)) == 0


def evaluate_anomaly_snapshots(db: Session) -> int:
    """Settle every unsettled snapshot whose match has since completed —
    same "only once results exist" gating as pricing_snapshot's own
    settlement (see app/pricing/snapshot_service.py)."""
    pending = db.scalars(select(AnomalyAlertSnapshot).where(AnomalyAlertSnapshot.evaluated_at.is_(None))).all()
    n_settled = 0
    now = datetime.now(timezone.utc)
    for snap in pending:
        match = db.get(Match, snap.match_id)
        if match is None or match.status != MatchStatus.COMPLETED:
            continue
        if snap.alert_type in (MODEL_VS_MARKET_DIVERGENCE, BOOKMAKER_VS_CONSENSUS_OUTLIER):
            _settle_divergence_or_outlier(db, snap)
        elif snap.alert_type in (STALE_AFTER_LINEUP_CHANGE, STALE_AFTER_CONTEXT_CHANGE):
            _settle_staleness(db, snap)
        elif snap.alert_type in (NON_MONOTONIC_PLAYER_PRICE_CURVE, ADJACENT_THRESHOLD_JUMP):
            _settle_curve(db, snap)
        snap.evaluated_at = now
        n_settled += 1
    db.commit()
    return n_settled
