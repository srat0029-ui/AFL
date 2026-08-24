"""Freeze / refresh / settle case-level prospective snapshots (items 1-2).
Reuses the exact pure functions the detector and the prior stage's
alert-level settlement already use — nothing here re-derives consensus,
outlier, staleness, or curve logic; it only re-runs those SAME functions
at a later point in time and compares the result to what was frozen.

"First detection" / "latest pre-kick snapshot" (item 2) are derived from
the REAL quote-timestamp history already recorded for this exact market
(the same technique app/market_monitor/movement.py already uses) rather
than from when this service happens to be called — so even a single
retroactive pass over historical data yields a genuine before/after
comparison spanning the market's real observed pre-kickoff lifetime, not
an artifact of script timing.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AnomalyCaseSnapshot, Bookmaker, Match, MatchStatus, OddsQuote, PlayerMatchStat, PlayerPropMarket
from app.player_modelling.market import PlayerMarket
from app.player_modelling.match_context_service import current_context_for_match
from app.player_modelling.live_report_query import current_lineup_for
from app.player_modelling.prop_settlement import _actual_stat_value, _settle_result, compute_team_market_result
from app.pricing.market_intelligence import player_market_intelligence, team_market_intelligence

from app.market_monitor.case_builder import AnomalyCase
from app.market_monitor.common import aware, dedupe_bookmaker_prices
from app.market_monitor.context_staleness import check_context_item_staleness, check_lineup_staleness
from app.market_monitor.curve_integrity import CurvePoint, check_monotonicity, find_adjacent_jumps
from app.market_monitor.inbox import RankedCase
from app.market_monitor.movement import build_bookmaker_series
from app.market_monitor.outcome_taxonomy import classify_outcomes
from app.market_monitor.priority import TIER_CRITICAL, TIER_HIGH_PRIORITY
from app.market_monitor.types import (
    ADJACENT_THRESHOLD_JUMP,
    BOOKMAKER_VS_CONSENSUS_OUTLIER,
    NON_MONOTONIC_PLAYER_PRICE_CURVE,
    STALE_AFTER_CONTEXT_CHANGE,
    STALE_AFTER_LINEUP_CHANGE,
)


def _market_quotes(db: Session, case: AnomalyCase, now: datetime) -> list:
    """Never returns a quote recorded AFTER `now` — the same leakage
    discipline used throughout this codebase's point-in-time feature
    builders, applied here so a case's frozen evidence can never include
    information that didn't exist yet at freeze time (item 10's own
    explicit test target)."""
    if case.player_id is not None:
        return db.scalars(
            select(PlayerPropMarket).where(
                PlayerPropMarket.match_id == case.match_id, PlayerPropMarket.player_id == case.player_id,
                PlayerPropMarket.market_type == case.market_type, PlayerPropMarket.threshold == case.threshold,
                PlayerPropMarket.selection.in_((None, "over")), PlayerPropMarket.recorded_at <= now,
            )
        ).all()
    return db.scalars(
        select(OddsQuote).where(
            OddsQuote.match_id == case.match_id, OddsQuote.market_type == case.market_type,
            OddsQuote.selection == case.selection, OddsQuote.line_value == case.line_value, OddsQuote.recorded_at <= now,
        )
    ).all()


def _consensus_span(db: Session, case: AnomalyCase, now: datetime | None = None) -> tuple[float | None, float | None, datetime | None, datetime | None, int]:
    """Mean implied probability across all bookmakers seen at the EARLIEST
    observed timestamp vs the LATEST — see module docstring. Falls back to
    the case's own already-computed consensus (single point) when there's
    no real historical span to work with."""
    now = now or datetime.now(timezone.utc)
    quotes = _market_quotes(db, case, now)
    bookmaker_name_by_id = {b.id: b.name for b in db.scalars(select(Bookmaker)).all()}
    series = build_bookmaker_series(quotes, bookmaker_name_by_id=bookmaker_name_by_id)
    if not series:
        return None, None, None, None, 0
    first_probs = [s.first_probability for s in series]
    latest_probs = [s.latest_probability for s in series]
    first_at = min(s.first_at for s in series)
    latest_at = max(s.latest_at for s in series)
    return sum(first_probs) / len(first_probs), sum(latest_probs) / len(latest_probs), first_at, latest_at, len(series)


def freeze_or_refresh_case_snapshots(db: Session, ranked_cases: list[RankedCase], now: datetime | None = None) -> tuple[int, int]:
    """Returns (n_newly_frozen, n_refreshed). Only HIGH_PRIORITY/CRITICAL
    cases are ever frozen (item 1's own scope) — a case that later drops
    below that tier keeps its existing snapshot untouched (its evidence
    was real at the time; dropping tier isn't a reason to discard it)."""
    now = now or datetime.now(timezone.utc)
    n_new, n_refreshed = 0, 0
    for r in ranked_cases:
        if r.priority.tier not in (TIER_HIGH_PRIORITY, TIER_CRITICAL):
            continue
        c = r.case
        existing = db.scalar(select(AnomalyCaseSnapshot).where(AnomalyCaseSnapshot.case_id == c.case_id))
        match = db.get(Match, c.match_id)

        if existing is None:
            first_prob, latest_prob, first_at, latest_at, n_books = _consensus_span(db, c, now)
            hours_to_kickoff = None
            if match is not None:
                hours_to_kickoff = max((aware(match.scheduled_start) - now).total_seconds() / 3600.0, 0.0)
            snap = AnomalyCaseSnapshot(
                case_id=c.case_id, match_id=c.match_id, player_id=c.player_id, team_id=c.team_id,
                market_type=c.market_type, selection=c.selection, threshold=c.threshold, line_value=c.line_value,
                alert_types=[c.primary_alert.alert_type] + c.supporting_alert_types, priority_score=r.priority.total_score,
                priority_components=[{"name": comp.name, "raw_value": comp.raw_value, "normalized": comp.normalized, "weight": comp.weight, "contribution": comp.contribution} for comp in r.priority.components],
                model_probability=c.primary_alert.model_probability, model_version=c.primary_alert.model_version,
                market_consensus_probability_at_freeze=first_prob if first_prob is not None else c.primary_alert.market_consensus_probability,
                bookmaker_prices_at_freeze=[{"bookmaker_name": b.bookmaker_name, "price_decimal": b.price_decimal, "recorded_at": b.recorded_at.isoformat()} for b in dedupe_bookmaker_prices([b for a in c.alerts for b in a.bookmaker_prices])],
                n_bookmakers_at_freeze=n_books, earliest_quote_at=first_at, latest_quote_at_freeze=latest_at,
                lineup_status=c.primary_alert.lineup_status, context_state=c.primary_alert.context_state,
                model_risk_flags=[{"code": f.code, "description": f.description} for f in c.primary_alert.model_risk_flags],
                first_seen_at=now, persistence_n_snapshots_at_freeze=1, time_to_kickoff_hours_at_freeze=hours_to_kickoff,
                frozen_at=now,
                market_consensus_probability_latest=latest_prob if latest_prob is not None else c.primary_alert.market_consensus_probability,
                model_probability_latest=c.primary_alert.model_probability, n_bookmakers_latest=n_books,
                latest_observed_at=now, n_prekickoff_refreshes=0,
            )
            db.add(snap)
            db.flush()
            n_new += 1
        elif existing.resolved_at is None and match is not None and match.status == MatchStatus.SCHEDULED:
            _, latest_prob, _, latest_at, n_books = _consensus_span(db, c, now)
            existing.market_consensus_probability_latest = latest_prob if latest_prob is not None else existing.market_consensus_probability_latest
            existing.model_probability_latest = c.primary_alert.model_probability
            existing.n_bookmakers_latest = n_books or existing.n_bookmakers_latest
            existing.latest_observed_at = now
            existing.n_prekickoff_refreshes += 1
            n_refreshed += 1
    db.commit()
    return n_new, n_refreshed


def settle_case_snapshots(db: Session, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    pending = db.scalars(select(AnomalyCaseSnapshot).where(AnomalyCaseSnapshot.resolved_at.is_(None))).all()
    n = 0
    for snap in pending:
        match = db.get(Match, snap.match_id)
        if match is None or match.status != MatchStatus.COMPLETED:
            continue

        had_outlier = BOOKMAKER_VS_CONSENSUS_OUTLIER in (snap.alert_types or [])
        had_stale = any(t in (snap.alert_types or []) for t in (STALE_AFTER_LINEUP_CHANGE, STALE_AFTER_CONTEXT_CHANGE))
        had_curve = any(t in (snap.alert_types or []) for t in (NON_MONOTONIC_PLAYER_PRICE_CURVE, ADJACENT_THRESHOLD_JUMP))

        # --- outlier convergence: re-run market intelligence now, see if the still-eligible market still shows an outlier ---
        outlier_converged = None
        if had_outlier and snap.player_id is not None:
            intel = player_market_intelligence(db, snap.match_id, snap.player_id, snap.market_type, "over_under", snap.threshold, snap.model_probability or 0.0)
            outlier_converged = intel.outlier is None or not intel.outlier.is_outlier

        # --- stale-after-context: did a quote arrive after the frozen context event? re-run the same check with "now" as the reference point ---
        stale_repriced = None
        if had_stale and snap.player_id is not None:
            lineup = current_lineup_for(db, snap.player_id, snap.match_id)
            context_items = [i for i in current_context_for_match(db, snap.match_id) if i.player_id == snap.player_id]
            quotes = db.scalars(select(PlayerPropMarket).where(PlayerPropMarket.match_id == snap.match_id, PlayerPropMarket.player_id == snap.player_id, PlayerPropMarket.market_type == snap.market_type, PlayerPropMarket.threshold == snap.threshold)).all()
            if quotes:
                latest_at_settlement = max(q.recorded_at for q in quotes)
                lineup_result = check_lineup_staleness(lineup, latest_at_settlement) if lineup else None
                context_result = check_context_item_staleness(context_items, latest_at_settlement)
                stale_repriced = not ((lineup_result.is_stale if lineup_result else False) or context_result.is_stale)

        # --- curve resolution: re-run the same check against the CURRENT persisted projection, if one still exists ---
        curve_resolved = None
        if had_curve and snap.player_id is not None:
            from app.market_monitor.detector import price_single_match

            _, disposals, goals = price_single_match(db, snap.match_id)
            price = next((p for p in (disposals + goals) if p.player_id == snap.player_id), None)
            if price is not None:
                points = [CurvePoint(t.threshold, t.probability) for t in price.thresholds]
                if NON_MONOTONIC_PLAYER_PRICE_CURVE in (snap.alert_types or []):
                    curve_resolved = check_monotonicity(points).is_monotonic
                else:
                    curve_resolved = len(find_adjacent_jumps(points)) == 0

        # snap.market_consensus_probability_latest is already "the latest
        # observed consensus" (kept rolling by freeze_or_refresh_case_snapshots
        # while the match was still SCHEDULED) - once the match has
        # COMPLETED no further quotes arrive, so that rolling value IS the
        # final pre-kickoff consensus; no need to recompute it here.
        outcome_codes = classify_outcomes(
            consensus_at_freeze=snap.market_consensus_probability_at_freeze, consensus_at_settlement=snap.market_consensus_probability_latest,
            model_probability_at_freeze=snap.model_probability, model_probability_at_settlement=snap.model_probability_latest,
            had_outlier_alert=had_outlier, outlier_converged=outlier_converged, had_stale_context_alert=had_stale,
            stale_market_repriced=stale_repriced, had_curve_alert=had_curve, curve_anomaly_resolved=curve_resolved,
        )

        actual_value, actual_note = None, None
        if snap.player_id is not None:
            stat = db.scalar(select(PlayerMatchStat).where(PlayerMatchStat.match_id == snap.match_id, PlayerMatchStat.player_id == snap.player_id))
            if stat is not None:
                actual_value = _actual_stat_value(stat, snap.market_type)
                if actual_value is not None and snap.threshold is not None:
                    actual_note = _settle_result(actual_value, snap.threshold, "over_under")
        else:
            result = compute_team_market_result(match, snap.market_type, snap.selection, snap.line_value)
            if result is not None:
                actual_value, actual_note = result

        snap.outcome_codes = outcome_codes
        snap.outlier_converged = outlier_converged
        snap.stale_market_repriced = stale_repriced
        snap.curve_anomaly_resolved = curve_resolved
        snap.actual_stat_value = actual_value
        snap.actual_outcome_note = actual_note
        snap.resolved_at = now
        snap.time_to_resolution_hours = (now - aware(snap.frozen_at)).total_seconds() / 3600.0
        n += 1
    db.commit()
    return n
