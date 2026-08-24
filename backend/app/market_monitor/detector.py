"""Orchestrates every check in this package into one flat list of Alerts
for the current round — the single entry point the API/UI/verification
script all call. Reuses app.pricing.round_pricing.price_current_round for
"what is the model's own price right now" (never recomputes a model) and
app.pricing.market_intelligence for "what does the market say" (already
the validated consensus/outlier/devig engine — see common.py's docstring).

Every numeric threshold used to decide whether something is "an anomaly"
is a plain, documented module-level constant (never fit to outcomes, per
item 8's boundary applied consistently across this whole package).
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.edges.calculator import ModelsUnavailableError, build_model_context
from app.models import Bookmaker, ExpectedLineup, Match, OddsQuote, PlayerDisposalProjection, PlayerGoalProjection, PlayerPropMarket
from app.player_modelling.market import PlayerMarket
from app.player_modelling.match_context_service import current_context_for_match
from app.player_modelling.live_report_query import current_lineup_for
from app.pricing.market_intelligence import MarketIntelligence, player_market_intelligence, team_market_intelligence
from app.pricing.player_pricing import DisposalPrice, GoalPrice, price_disposals, price_goals
from app.pricing.round_pricing import RoundPricing, price_current_round
from app.pricing.team_pricing import TeamMarketPrice, latest_completed_match_timestamp, price_team_market

from app.market_monitor.common import aware, bookmaker_price_entries, model_risk_flag_entries
from app.market_monitor.context_staleness import REASON_CODE as CONTEXT_REASON_CODE, check_context_item_staleness, check_lineup_staleness
from app.market_monitor.curve_integrity import CurvePoint, check_monotonicity, find_adjacent_jumps
from app.market_monitor.movement import STABLE_MOVE_PP, SHARP_MOVE_PP, build_bookmaker_series, detect_movement_anomalies
from app.market_monitor.team_consistency import check_pair_consistency
from app.market_monitor.types import (
    BookmakerPriceEntry,
    ADJACENT_THRESHOLD_JUMP,
    BOOKMAKER_MOVED_VS_STABLE_CONSENSUS,
    BOOKMAKER_VS_CONSENSUS_OUTLIER,
    CONSENSUS_MOVED_VS_STALE_BOOKMAKER,
    LARGE_MARKET_DISPERSION,
    MODEL_VS_MARKET_DIVERGENCE,
    NON_MONOTONIC_PLAYER_PRICE_CURVE,
    SEVERITY_CRITICAL,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    SHARP_MARKET_MOVE_MODEL_STABLE,
    STALE_AFTER_CONTEXT_CHANGE,
    STALE_AFTER_LINEUP_CHANGE,
    TEAM_MARKET_INTERNAL_INCONSISTENCY,
    Alert,
)

# --- Documented thresholds ---------------------------------------------------

# Model-vs-consensus gap, in probability points, before it's worth a
# trading desk's attention. Below this is well within the normal noise of
# two independently-derived probabilities on the same event.
DIVERGENCE_WARN_PP = 0.08
DIVERGENCE_CRITICAL_PP = 0.15

# Spread (max-min) across eligible-book implied probabilities.
DISPERSION_WARN_PP = 0.10
DISPERSION_CRITICAL_PP = 0.20

MOVEMENT_CRITICAL_PP = 0.10  # see movement.py's SHARP_MOVE_PP for the "warning" floor


def _severity(value: float, warn: float, critical: float) -> str:
    if value >= critical:
        return SEVERITY_CRITICAL
    if value >= warn:
        return SEVERITY_WARNING
    return SEVERITY_INFO


def _latest_quote_at(intel: MarketIntelligence) -> datetime | None:
    return max((b.recorded_at for b in intel.books), default=None)


def _freshness_of(intel: MarketIntelligence) -> str | None:
    from app.player_modelling.prop_odds_freshness import freshness_state

    latest = _latest_quote_at(intel)
    return freshness_state(aware(latest)) if latest is not None else None


@dataclass(frozen=True)
class _Common:
    match_id: int
    home_team: str
    away_team: str
    generated_at: datetime


def _alert(
    common: _Common, *, alert_type: str, severity: str, reason_code: str, detail: str, market_type: str,
    player_id: int | None = None, player_name: str | None = None, team_id: int | None = None,
    selection: str | None = None, threshold: float | None = None, line_value: float | None = None,
    model_probability: float | None = None, model_fair_odds: float | None = None,
    market_consensus_probability: float | None = None, bookmaker_prices=(), freshness: str | None = None,
    model_version: str | None = None, lineup_status: str | None = None, context_state: str | None = None,
    model_risk_flags=(), magnitude: float | None = None,
) -> Alert:
    return Alert(
        alert_type=alert_type, severity=severity, reason_code=reason_code, detail=detail,
        match_id=common.match_id, home_team=common.home_team, away_team=common.away_team,
        player_id=player_id, player_name=player_name, team_id=team_id,
        market_type=market_type, selection=selection, threshold=threshold, line_value=line_value,
        model_probability=model_probability, model_fair_odds=model_fair_odds,
        market_consensus_probability=market_consensus_probability, bookmaker_prices=list(bookmaker_prices),
        freshness=freshness, model_version=model_version, lineup_status=lineup_status, context_state=context_state,
        model_risk_flags=list(model_risk_flags), generated_at=common.generated_at, magnitude=magnitude,
    )


def _divergence_and_outlier_and_dispersion_alerts(
    common: _Common, intel: MarketIntelligence, *, market_type: str, selection: str | None, threshold: float | None,
    line_value: float | None, model_fair_odds: float, player_id=None, player_name=None, team_id=None,
    model_version=None, lineup_status=None, model_risk_flags=(),
) -> list[Alert]:
    if not intel.has_market:
        return []
    alerts = []
    books = bookmaker_price_entries(intel.books)
    freshness = _freshness_of(intel)

    if intel.difference_pp is not None and abs(intel.difference_pp) >= DIVERGENCE_WARN_PP:
        sev = _severity(abs(intel.difference_pp), DIVERGENCE_WARN_PP, DIVERGENCE_CRITICAL_PP)
        alerts.append(_alert(
            common, alert_type=MODEL_VS_MARKET_DIVERGENCE, severity=sev, reason_code=f"divergence_{abs(intel.difference_pp) * 100:.1f}pp",
            detail=f"Model probability ({intel.model_probability:.3f}) differs from market consensus ({intel.market_implied_probability:.3f}) by {intel.difference_pp * 100:+.1f}pp.",
            market_type=market_type, player_id=player_id, player_name=player_name, team_id=team_id, selection=selection,
            threshold=threshold, line_value=line_value, model_probability=intel.model_probability, model_fair_odds=model_fair_odds,
            market_consensus_probability=intel.market_implied_probability, bookmaker_prices=books, freshness=freshness,
            model_version=model_version, lineup_status=lineup_status, model_risk_flags=model_risk_flags,
            magnitude=abs(intel.difference_pp),
        ))

    if intel.outlier is not None and intel.outlier.is_outlier:
        sev = _severity(intel.outlier.pct_difference, 20.0, 40.0)
        alerts.append(_alert(
            common, alert_type=BOOKMAKER_VS_CONSENSUS_OUTLIER, severity=sev, reason_code=f"outlier_{intel.outlier.pct_difference:.1f}pct",
            detail=f"Best price {intel.outlier.best_price:.2f} is {intel.outlier.pct_difference:.1f}% away from the median of the rest of the eligible market ({intel.outlier.median_eligible_price:.2f}).",
            market_type=market_type, player_id=player_id, player_name=player_name, team_id=team_id, selection=selection,
            threshold=threshold, line_value=line_value, model_probability=intel.model_probability, model_fair_odds=model_fair_odds,
            market_consensus_probability=intel.market_implied_probability, bookmaker_prices=books, freshness=freshness,
            model_version=model_version, lineup_status=lineup_status, model_risk_flags=model_risk_flags,
            magnitude=intel.outlier.pct_difference,
        ))

    if intel.consensus is not None and intel.consensus.spread >= DISPERSION_WARN_PP:
        sev = _severity(intel.consensus.spread, DISPERSION_WARN_PP, DISPERSION_CRITICAL_PP)
        alerts.append(_alert(
            common, alert_type=LARGE_MARKET_DISPERSION, severity=sev, reason_code=f"dispersion_{intel.consensus.spread * 100:.1f}pp",
            detail=f"Eligible-book implied probabilities span {intel.consensus.spread * 100:.1f}pp (n={intel.consensus.n_bookmakers}) — the market is not tightly agreed on this price.",
            market_type=market_type, player_id=player_id, player_name=player_name, team_id=team_id, selection=selection,
            threshold=threshold, line_value=line_value, model_probability=intel.model_probability, model_fair_odds=model_fair_odds,
            market_consensus_probability=intel.market_implied_probability, bookmaker_prices=books, freshness=freshness,
            model_version=model_version, lineup_status=lineup_status, model_risk_flags=model_risk_flags,
            magnitude=intel.consensus.spread,
        ))
    return alerts


def _movement_alerts(
    common: _Common, quotes: list, bookmaker_name_by_id: dict[int, str], *, market_type: str, selection, threshold,
    line_value, model_probability, model_fair_odds, player_id=None, player_name=None, team_id=None,
) -> list[Alert]:
    series = build_bookmaker_series(quotes, bookmaker_name_by_id=bookmaker_name_by_id)
    findings = detect_movement_anomalies(series)
    alerts = []
    for f in findings:
        if f.kind == "sharp_consensus_move":
            move = abs(f.consensus_latest_pp - f.consensus_first_pp)
            alerts.append(_alert(
                common, alert_type=SHARP_MARKET_MOVE_MODEL_STABLE, severity=_severity(move, SHARP_MOVE_PP, MOVEMENT_CRITICAL_PP),
                reason_code=f"consensus_move_{move * 100:.1f}pp",
                detail=f"Consensus implied probability moved from {f.consensus_first_pp:.3f} to {f.consensus_latest_pp:.3f} while the model's current probability is {model_probability:.3f}.",
                market_type=market_type, player_id=player_id, player_name=player_name, team_id=team_id, selection=selection,
                threshold=threshold, line_value=line_value, model_probability=model_probability, model_fair_odds=model_fair_odds,
                magnitude=move,
            ))
        elif f.kind == "bookmaker_diverges":
            b = f.bookmaker
            alerts.append(_alert(
                common, alert_type=BOOKMAKER_MOVED_VS_STABLE_CONSENSUS, severity=_severity(b.moved_pp, SHARP_MOVE_PP, MOVEMENT_CRITICAL_PP),
                reason_code=f"{b.bookmaker_name}_moved_{b.moved_pp * 100:.1f}pp",
                detail=f"{b.bookmaker_name} moved from {b.first_price:.2f} to {b.latest_price:.2f} ({b.moved_pp * 100:.1f}pp) while the rest of the eligible market stayed within {STABLE_MOVE_PP * 100:.1f}pp.",
                market_type=market_type, player_id=player_id, player_name=player_name, team_id=team_id, selection=selection,
                threshold=threshold, line_value=line_value, model_probability=model_probability, model_fair_odds=model_fair_odds,
                bookmaker_prices=[BookmakerPriceEntry(b.bookmaker_name, b.latest_price, b.latest_at, "included")],
                magnitude=b.moved_pp,
            ))
        elif f.kind == "bookmaker_stale_vs_consensus":
            b = f.bookmaker
            move = abs(f.consensus_latest_pp - f.consensus_first_pp)
            alerts.append(_alert(
                common, alert_type=CONSENSUS_MOVED_VS_STALE_BOOKMAKER, severity=SEVERITY_WARNING,
                reason_code=f"{b.bookmaker_name}_stale_vs_consensus",
                detail=f"Consensus moved from {f.consensus_first_pp:.3f} to {f.consensus_latest_pp:.3f} while {b.bookmaker_name}'s price has not changed since {b.first_at.isoformat()}.",
                market_type=market_type, player_id=player_id, player_name=player_name, team_id=team_id, selection=selection,
                threshold=threshold, line_value=line_value, model_probability=model_probability, model_fair_odds=model_fair_odds,
                bookmaker_prices=[BookmakerPriceEntry(b.bookmaker_name, b.latest_price, b.latest_at, "included")],
                magnitude=move,
            ))
    return alerts


def _curve_alerts(
    common: _Common, *, market_type: str, player_id: int, player_name: str, model_points: list[CurvePoint],
    market_points: list[CurvePoint], model_version: str | None, lineup_status: str | None, model_risk_flags,
) -> list[Alert]:
    alerts = []
    for label, points in (("model", model_points), ("market", market_points)):
        if len(points) < 2:
            continue
        mono = check_monotonicity(points)
        for lo, hi in mono.violations:
            alerts.append(_alert(
                common, alert_type=NON_MONOTONIC_PLAYER_PRICE_CURVE,
                severity=_severity(hi.probability - lo.probability, 0.01, 0.05),
                reason_code=f"{label}_non_monotonic_{lo.threshold:g}_{hi.threshold:g}",
                detail=f"{label.capitalize()} P(>= {hi.threshold:g}) = {hi.probability:.3f} is higher than P(>= {lo.threshold:g}) = {lo.probability:.3f} — a lower threshold must never have a lower probability than a higher one.",
                market_type=market_type, player_id=player_id, player_name=player_name, threshold=hi.threshold,
                model_version=model_version, lineup_status=lineup_status, model_risk_flags=model_risk_flags,
                magnitude=hi.probability - lo.probability,
            ))
        if label != "market":
            # A smooth NB/hurdle survival function's gaps track wherever
            # the distribution's mode sits relative to the preset
            # threshold ladder — a large first gap for a player whose mean
            # sits near the low end of the thresholds is completely normal
            # curve shape, not an anomaly (confirmed empirically: this
            # pattern is near-universal across real players, not a rare
            # outlier). "Unusual jump" is only a meaningful trading-desk
            # signal on the MARKET curve, where each point is an
            # independently-quoted bookmaker line that could genuinely be
            # stale/mispriced relative to its neighbours (item 2's own
            # framing: "one threshold materially diverging").
            continue
        for jump in find_adjacent_jumps(points):
            alerts.append(_alert(
                common, alert_type=ADJACENT_THRESHOLD_JUMP, severity=SEVERITY_WARNING,
                reason_code=f"{label}_jump_{jump.lower.threshold:g}_{jump.upper.threshold:g}",
                detail=f"{label.capitalize()} probability drops {jump.gap * 100:.1f}pp between {jump.lower.threshold:g} and {jump.upper.threshold:g} — {jump.gap / jump.other_gaps_median:.1f}x its neighbouring gap(s) ({jump.other_gaps_median * 100:.1f}pp) on this same curve.",
                market_type=market_type, player_id=player_id, player_name=player_name, threshold=jump.upper.threshold,
                model_version=model_version, lineup_status=lineup_status, model_risk_flags=model_risk_flags,
                magnitude=jump.gap / jump.other_gaps_median,  # item 8's "neighbouring-line support" ratio, unchanged from the fix
            ))
    return alerts


def _staleness_alerts(
    common: _Common, *, market_type: str, player_id: int | None, player_name: str | None, team_id: int | None,
    lineup: ExpectedLineup | None, context_items: list, latest_quote_at: datetime | None, model_probability: float,
    model_fair_odds: float, model_version: str | None, model_risk_flags, threshold: float | None = None, selection: str | None = None,
) -> list[Alert]:
    if latest_quote_at is None:
        return []
    alerts = []
    lineup_result = check_lineup_staleness(lineup, latest_quote_at) if lineup is not None else None
    if lineup_result is not None and lineup_result.is_stale:
        alerts.append(_alert(
            common, alert_type=STALE_AFTER_LINEUP_CHANGE, severity=SEVERITY_WARNING, reason_code=CONTEXT_REASON_CODE,
            detail=f"{lineup_result.context_description} The latest bookmaker quote for this market was recorded {aware(latest_quote_at).isoformat()}, before that.",
            market_type=market_type, player_id=player_id, player_name=player_name, team_id=team_id, selection=selection,
            threshold=threshold, model_probability=model_probability, model_fair_odds=model_fair_odds,
            model_version=model_version, lineup_status=lineup.selection_status if lineup else None,
            context_state=lineup_result.context_description, model_risk_flags=model_risk_flags,
        ))
    context_result = check_context_item_staleness(context_items, latest_quote_at)
    if context_result.is_stale:
        alerts.append(_alert(
            common, alert_type=STALE_AFTER_CONTEXT_CHANGE, severity=SEVERITY_WARNING, reason_code=CONTEXT_REASON_CODE,
            detail=f"{context_result.context_description} The latest bookmaker quote for this market was recorded {aware(latest_quote_at).isoformat()}, before that.",
            market_type=market_type, player_id=player_id, player_name=player_name, team_id=team_id, selection=selection,
            threshold=threshold, model_probability=model_probability, model_fair_odds=model_fair_odds,
            model_version=model_version, lineup_status=lineup.selection_status if lineup else None,
            context_state=context_result.context_description, model_risk_flags=model_risk_flags,
        ))
    return alerts


def _player_prop_quotes(db: Session, *, match_id: int, player_id: int, market_type: str, line_type: str, threshold: float) -> list:
    return db.scalars(
        select(PlayerPropMarket).where(
            PlayerPropMarket.match_id == match_id, PlayerPropMarket.player_id == player_id, PlayerPropMarket.market_type == market_type,
            PlayerPropMarket.line_type == line_type, PlayerPropMarket.threshold == threshold, PlayerPropMarket.selection.in_((None, "over")),
        )
    ).all()


def detect_player_family_anomalies(db: Session, price: DisposalPrice | GoalPrice, common: _Common, market_type: str) -> list[Alert]:
    lineup = current_lineup_for(db, price.player_id, price.match_id)
    context_items = [i for i in current_context_for_match(db, price.match_id) if i.player_id == price.player_id]
    bookmaker_name_by_id = {b.id: b.name for b in db.scalars(select(Bookmaker)).all()}
    model_risk_flags = model_risk_flag_entries(price.model_risk_flags)

    thresholds = sorted(price.thresholds, key=lambda t: t.threshold)
    model_points = [CurvePoint(t.threshold, t.probability) for t in thresholds]
    market_points: list[CurvePoint] = []
    alerts: list[Alert] = []

    for t in thresholds:
        intel = player_market_intelligence(db, price.match_id, price.player_id, market_type, t.line_type, t.threshold, t.probability)
        alerts += _divergence_and_outlier_and_dispersion_alerts(
            common, intel, market_type=market_type, selection="over", threshold=t.threshold, line_value=None, model_fair_odds=t.fair_odds,
            player_id=price.player_id, player_name=price.player_name, model_version=price.model_version,
            lineup_status=price.lineup_status, model_risk_flags=model_risk_flags,
        )
        if not intel.has_market:
            continue
        market_points.append(CurvePoint(t.threshold, intel.market_implied_probability))

        latest_quote_at = _latest_quote_at(intel)
        alerts += _staleness_alerts(
            common, market_type=market_type, player_id=price.player_id, player_name=price.player_name, team_id=price.team_id,
            lineup=lineup, context_items=context_items, latest_quote_at=latest_quote_at, model_probability=t.probability,
            model_fair_odds=t.fair_odds, model_version=price.model_version, model_risk_flags=model_risk_flags,
            threshold=t.threshold, selection="over",
        )

        quotes = _player_prop_quotes(db, match_id=price.match_id, player_id=price.player_id, market_type=market_type, line_type=t.line_type, threshold=t.threshold)
        alerts += _movement_alerts(
            common, quotes, bookmaker_name_by_id, market_type=market_type, selection="over", threshold=t.threshold, line_value=None,
            model_probability=t.probability, model_fair_odds=t.fair_odds, player_id=price.player_id, player_name=price.player_name,
        )

    alerts += _curve_alerts(
        common, market_type=market_type, player_id=price.player_id, player_name=price.player_name, model_points=model_points,
        market_points=market_points, model_version=price.model_version, lineup_status=price.lineup_status, model_risk_flags=model_risk_flags,
    )
    return alerts


def detect_team_match_anomalies(db: Session, team: TeamMarketPrice, common: _Common) -> list[Alert]:
    alerts: list[Alert] = []
    bookmaker_name_by_id = {b.id: b.name for b in db.scalars(select(Bookmaker)).all()}

    intel = team_market_intelligence(db, common.match_id, "h2h", team.home_team, None, team.home_win_probability)
    alerts += _divergence_and_outlier_and_dispersion_alerts(
        common, intel, market_type="h2h", selection=team.home_team, threshold=None, line_value=None, model_fair_odds=team.home_fair_odds,
        model_version=team.model_version,
    )
    latest_quote_at = _latest_quote_at(intel)
    team_context = [i for i in current_context_for_match(db, common.match_id) if i.player_id is None]
    alerts += _staleness_alerts(
        common, market_type="h2h", player_id=None, player_name=None, team_id=None, lineup=None, context_items=team_context,
        latest_quote_at=latest_quote_at, model_probability=team.home_win_probability, model_fair_odds=team.home_fair_odds,
        model_version=team.model_version, model_risk_flags=(), selection=team.home_team,
    )
    h2h_quotes = db.scalars(select(OddsQuote).where(OddsQuote.match_id == common.match_id, OddsQuote.market_type == "h2h", OddsQuote.selection == team.home_team)).all()
    alerts += _movement_alerts(
        common, h2h_quotes, bookmaker_name_by_id, market_type="h2h", selection=team.home_team, threshold=None, line_value=None,
        model_probability=team.home_win_probability, model_fair_odds=team.home_fair_odds,
    )

    # TEAM_MARKET_INTERNAL_INCONSISTENCY: each bookmaker's own two-sided h2h quote
    all_h2h = db.scalars(select(OddsQuote).where(OddsQuote.match_id == common.match_id, OddsQuote.market_type == "h2h")).all()
    latest_by_book_selection: dict[tuple[int, str], OddsQuote] = {}
    for q in all_h2h:
        key = (q.bookmaker_id, q.selection)
        if key not in latest_by_book_selection or q.recorded_at > latest_by_book_selection[key].recorded_at:
            latest_by_book_selection[key] = q
    for bookmaker_id in {q.bookmaker_id for q in all_h2h}:
        home_q = latest_by_book_selection.get((bookmaker_id, team.home_team))
        away_q = latest_by_book_selection.get((bookmaker_id, team.away_team))
        if home_q is None or away_q is None:
            continue
        name = bookmaker_name_by_id.get(bookmaker_id, str(bookmaker_id))
        pair = check_pair_consistency(name, home_q.price_decimal, away_q.price_decimal)
        if pair.is_inconsistent:
            alerts.append(_alert(
                common, alert_type=TEAM_MARKET_INTERNAL_INCONSISTENCY, severity=SEVERITY_CRITICAL if pair.combined_probability < 1.0 else SEVERITY_WARNING,
                reason_code=f"pair_combined_{pair.combined_probability:.3f}", detail=pair.description, market_type="h2h",
                selection=team.home_team, bookmaker_prices=[
                    BookmakerPriceEntry(name, home_q.price_decimal, home_q.recorded_at, "included"),
                    BookmakerPriceEntry(name, away_q.price_decimal, away_q.recorded_at, "included"),
                ],
                model_version=team.model_version, magnitude=abs(1.0 - pair.combined_probability),
            ))
    return alerts


def detect_round_anomalies(db: Session, round_pricing: RoundPricing | None = None) -> list[Alert]:
    round_pricing = round_pricing or price_current_round(db, use_cache=False)
    now = datetime.now(timezone.utc)
    match_names: dict[int, tuple[str, str]] = {t.match_id: (t.home_team, t.away_team) for t in round_pricing.teams}

    alerts: list[Alert] = []
    for team in round_pricing.teams:
        common = _Common(match_id=team.match_id, home_team=team.home_team, away_team=team.away_team, generated_at=now)
        alerts += detect_team_match_anomalies(db, team, common)

    for price in round_pricing.disposals:
        home, away = match_names.get(price.match_id, ("", ""))
        common = _Common(match_id=price.match_id, home_team=home, away_team=away, generated_at=now)
        alerts += detect_player_family_anomalies(db, price, common, PlayerMarket.DISPOSALS.value)

    for price in round_pricing.goals:
        home, away = match_names.get(price.match_id, ("", ""))
        common = _Common(match_id=price.match_id, home_team=home, away_team=away, generated_at=now)
        alerts += detect_player_family_anomalies(db, price, common, PlayerMarket.GOALS.value)

    return alerts


def price_single_match(db: Session, match_id: int) -> tuple[TeamMarketPrice | None, list[DisposalPrice], list[GoalPrice]]:
    """Prices exactly one match, independent of "current round" — item 6's
    `/matches/{match_id}` endpoint (and item 10's verification, which needs
    to inspect a specific match with real persisted player projections,
    not just whatever the single nearest upcoming round happens to have
    lineups for yet) both need this, not just the round-wide view."""
    match = db.get(Match, match_id)
    team = None
    if match is not None:
        try:
            context = build_model_context(db)
            now = datetime.now(timezone.utc)
            data_cutoff = latest_completed_match_timestamp(db) or now
            team = price_team_market(match, context, now, data_cutoff)
        except ModelsUnavailableError:
            team = None
    disposals = [price_disposals(db, row) for row in db.scalars(select(PlayerDisposalProjection).where(PlayerDisposalProjection.match_id == match_id)).all()]
    goals = [price_goals(db, row) for row in db.scalars(select(PlayerGoalProjection).where(PlayerGoalProjection.match_id == match_id)).all()]
    return team, disposals, goals


def detect_match_anomalies(db: Session, match_id: int) -> list[Alert]:
    match = db.get(Match, match_id)
    if match is None:
        return []
    team, disposals, goals = price_single_match(db, match_id)
    home_name, away_name = (team.home_team, team.away_team) if team is not None else (match.home_team.name, match.away_team.name)
    now = datetime.now(timezone.utc)
    common = _Common(match_id=match_id, home_team=home_name, away_team=away_name, generated_at=now)

    alerts: list[Alert] = []
    if team is not None:
        alerts += detect_team_match_anomalies(db, team, common)
    for price in disposals:
        alerts += detect_player_family_anomalies(db, price, common, PlayerMarket.DISPOSALS.value)
    for price in goals:
        alerts += detect_player_family_anomalies(db, price, common, PlayerMarket.GOALS.value)
    return alerts


def matches_with_projections(db: Session) -> list[int]:
    """Every match with at least one persisted disposal or goal projection
    — a broader real-data scan than "current round" (item 10's
    verification wants real examples, not just whatever round the live
    cycle happens to have lineups for at the moment this runs)."""
    disposal_ids = set(db.scalars(select(PlayerDisposalProjection.match_id).distinct()).all())
    goal_ids = set(db.scalars(select(PlayerGoalProjection.match_id).distinct()).all())
    return sorted(disposal_ids | goal_ids)


def active_match_ids(db: Session) -> list[int]:
    """Every still-SCHEDULED match with at least one persisted projection
    — "active" for trading-QA purposes (a completed match is no longer a
    live market to monitor). Used by the API's anomalies/summary
    endpoints, which need real breadth rather than depending on whichever
    single round the live cycle currently has lineups announced for."""
    from app.models import MatchStatus

    candidate_ids = matches_with_projections(db)
    if not candidate_ids:
        return []
    return sorted(db.scalars(select(Match.id).where(Match.id.in_(candidate_ids), Match.status == MatchStatus.SCHEDULED)).all())
