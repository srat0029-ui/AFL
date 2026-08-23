"""Reads persisted live projections (app/player_modelling/live_persistence.py)
for the API layer — mirrors disposal_report_query.py/goal_report_query.py's
"don't recompute, just read what's persisted" convention, with one
addition: arbitrary-line pricing (Section 4) reconstructs the distribution
object from stored parameters and prices it on demand, so nothing about
"which lines are supported" is baked into what got persisted.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ExpectedLineup,
    GoalModelRun,
    GoalModelValidationMetric,
    PlayerDisposalProjection,
    PlayerGoalProjection,
    PlayerModelRun,
    PlayerModelValidationMetric,
    PlayerPropMarket,
    SelectionStatus,
)
from app.player_modelling.disposal_distribution import NegativeBinomialDistribution
from app.player_modelling.goal_distribution import HurdleDistribution, NegativeBinomialGoalDistribution
from app.player_modelling.live_confidence import rare_event_warning
from app.player_modelling.live_engine import compute_team_model_version
from app.player_modelling.live_staleness import check_staleness
from app.player_modelling.market import PlayerMarket
from app.player_modelling.prop_math import categorize_edge, compare_model_to_market
from app.player_modelling.team_selection_ingestion import derive_announcement_state
from app.player_modelling.upcoming_team_context import build_upcoming_team_context
from app.player_modelling.upcoming_features import load_next_upcoming_round
from app.player_modelling.usage_regime import goal_usage_risk_flags

DISPOSAL_THRESHOLDS: tuple[int, ...] = (15, 20, 25, 30, 35, 40)
GOAL_THRESHOLDS: tuple[int, ...] = (1, 2, 3, 4, 5)

CONFIDENCE_RANK = {"insufficient_history": 0, "lower_confidence": 1, "moderate_confidence": 2, "higher_confidence": 3}


def disposal_distribution_for(p: PlayerDisposalProjection) -> NegativeBinomialDistribution:
    return NegativeBinomialDistribution(mu=p.predicted_mean, alpha=p.nb_alpha)


def goal_distribution_for(p: PlayerGoalProjection):
    if p.distribution_kind == "hurdle":
        return HurdleDistribution(p_score=p.p_score, mu_scored=p.mu_scored, alpha_scored=p.alpha_scored)
    return NegativeBinomialGoalDistribution(mu=p.predicted_mean, alpha=p.nb_alpha)


def price_line(dist, threshold: float, line_type: str) -> float:
    """line_type: "multi_plus" (>= threshold) or "over_under" (> threshold,
    e.g. 27.5) — the same two shapes app/player_modelling/market.py's
    LineType already defines, reused here rather than a third convention."""
    if line_type == "multi_plus":
        return dist.prob_at_least(threshold)
    return dist.prob_over(threshold)


def current_disposal_model_version(db: Session) -> str | None:
    run = db.scalar(select(PlayerModelRun).where(PlayerModelRun.is_promoted.is_(True)))
    return f"{run.model_name}@{run.run_at.isoformat()}" if run else None


def current_goal_model_version(db: Session) -> str | None:
    run = db.scalar(select(GoalModelRun).where(GoalModelRun.is_promoted.is_(True)))
    return f"{run.model_name}@{run.run_at.isoformat()}" if run else None


def current_lineup_for(db: Session, player_id: int, match_id: int) -> ExpectedLineup | None:
    return db.scalar(select(ExpectedLineup).where(ExpectedLineup.player_id == player_id, ExpectedLineup.match_id == match_id))


def _disposal_view(
    db: Session, row: PlayerDisposalProjection, current_model_version: str | None, current_team_model_version: str | None = None
) -> dict:
    dist = disposal_distribution_for(row)
    thresholds = {}
    for t in DISPOSAL_THRESHOLDS:
        prob = dist.prob_at_least(t)
        thresholds[str(t)] = {"probability": prob, "warning": rare_event_warning(PlayerMarket.DISPOSALS.value, float(t), prob)}

    current_lineup = current_lineup_for(db, row.player_id, row.match_id)
    staleness = check_staleness(
        projection_model_version=row.model_version,
        projection_data_cutoff=row.data_cutoff,
        projection_lineup_status=row.lineup_status_at_generation,
        current_model_version=current_model_version,
        current_data_cutoff=None,
        current_lineup_status=current_lineup.status if current_lineup else None,
        projection_team_model_version=row.team_model_version,
        current_team_model_version=current_team_model_version,
    )

    match = row.match
    return {
        "match_id": row.match_id,
        "player_id": row.player_id,
        "player_name": row.player.display_name,
        "team_id": row.team_id,
        "team_name": row.team.name,
        "round_number": match.round.round_number,
        "season_year": match.season.year,
        "scheduled_start": match.scheduled_start,
        "model_name": row.model_name,
        "model_version": row.model_version,
        "generated_at": row.generated_at,
        "data_cutoff": row.data_cutoff,
        "lineup_status": row.lineup_status_at_generation,
        "selection_status": current_lineup.selection_status if current_lineup else "uncertain",
        "is_confirmed": current_lineup.is_confirmed if current_lineup else False,
        "games_of_history": row.games_of_history,
        "expected": dist.mean(),
        "median": dist.median(),
        "interval_50": dist.interval(0.5),
        "interval_80": dist.interval(0.8),
        "interval_90": dist.interval(0.9),
        "thresholds": thresholds,
        "confidence_tier": row.confidence_tier,
        "warnings": row.warnings,
        "is_stale": staleness.is_stale,
        "stale_reasons": staleness.reasons,
        "input_features": row.input_features,
        "usage_regime": row.usage_regime,
        "model_risk_flags": [],  # disposal's usage-change effect didn't meet the evidence bar for a flag (see usage_regime.goal_usage_risk_flags)
    }


def _goal_view(
    db: Session, row: PlayerGoalProjection, current_model_version: str | None, current_team_model_version: str | None = None
) -> dict:
    dist = goal_distribution_for(row)
    thresholds = {}
    for t in GOAL_THRESHOLDS:
        prob = dist.prob_at_least(t)
        thresholds[str(t)] = {"probability": prob, "warning": rare_event_warning(PlayerMarket.GOALS.value, float(t), prob)}

    current_lineup = current_lineup_for(db, row.player_id, row.match_id)
    staleness = check_staleness(
        projection_model_version=row.model_version,
        projection_data_cutoff=row.data_cutoff,
        projection_lineup_status=row.lineup_status_at_generation,
        current_model_version=current_model_version,
        current_data_cutoff=None,
        current_lineup_status=current_lineup.status if current_lineup else None,
        projection_team_model_version=row.team_model_version,
        current_team_model_version=current_team_model_version,
    )

    match = row.match
    return {
        "match_id": row.match_id,
        "player_id": row.player_id,
        "player_name": row.player.display_name,
        "team_id": row.team_id,
        "team_name": row.team.name,
        "round_number": match.round.round_number,
        "season_year": match.season.year,
        "scheduled_start": match.scheduled_start,
        "model_name": row.model_name,
        "model_version": row.model_version,
        "generated_at": row.generated_at,
        "data_cutoff": row.data_cutoff,
        "lineup_status": row.lineup_status_at_generation,
        "selection_status": current_lineup.selection_status if current_lineup else "uncertain",
        "is_confirmed": current_lineup.is_confirmed if current_lineup else False,
        "games_of_history": row.games_of_history,
        "expected": dist.mean(),
        "thresholds": thresholds,
        "scoring_archetype": row.scoring_archetype,
        "confidence_tier": row.confidence_tier,
        "warnings": row.warnings,
        "is_stale": staleness.is_stale,
        "stale_reasons": staleness.reasons,
        "input_features": row.input_features,
        "usage_regime": row.usage_regime,
        "model_risk_flags": [{"code": f.code, "description": f.description} for f in goal_usage_risk_flags(row.usage_regime)],
    }


def load_match_projections(db: Session, match_id: int) -> dict:
    disposal_rows = db.scalars(
        select(PlayerDisposalProjection).where(PlayerDisposalProjection.match_id == match_id)
    ).all()
    goal_rows = db.scalars(select(PlayerGoalProjection).where(PlayerGoalProjection.match_id == match_id)).all()
    current_disposal_version = current_disposal_model_version(db)
    current_goal_version = current_goal_model_version(db)
    current_team_version = compute_team_model_version(db)
    return {
        "match_id": match_id,
        "disposals": [_disposal_view(db, r, current_disposal_version, current_team_version) for r in disposal_rows],
        "goals": [_goal_view(db, r, current_goal_version, current_team_version) for r in goal_rows],
    }


#: Section 9's three lineup-certainty tiers for Player Insights, strictest
#: first. "confirmed_out" players are EXCLUDED unconditionally at every
#: tier, not just the strict ones (Section 10) - a confirmed-out player is
#: never a useful "insight" to show in a consumer-facing view.
LINEUP_FILTER_CONFIRMED_ONLY = "confirmed_only"
LINEUP_FILTER_CONFIRMED_PLUS_EXPECTED = "confirmed_plus_expected"
LINEUP_FILTER_INCLUDE_UNCERTAIN = "include_uncertain"

EXPECTED_IN_SELECTION_STATUSES = {"confirmed_selected", "substitute"}


@dataclass(frozen=True)
class ProjectionFilters:
    round_number: int | None = None
    season_year: int | None = None
    team_id: int | None = None
    match_id: int | None = None
    confidence: str | None = None
    min_probability: float | None = None
    probability_threshold: float | None = None  # which threshold min_probability is measured against
    lineup_filter: str | None = None  # LINEUP_FILTER_* - None behaves like LINEUP_FILTER_INCLUDE_UNCERTAIN


def _apply_common_filters(views: list[dict], filters: ProjectionFilters) -> list[dict]:
    result = views
    if filters.round_number is not None:
        result = [v for v in result if v["round_number"] == filters.round_number]
    if filters.season_year is not None:
        result = [v for v in result if v["season_year"] == filters.season_year]
    if filters.team_id is not None:
        result = [v for v in result if v["team_id"] == filters.team_id]
    if filters.match_id is not None:
        result = [v for v in result if v["match_id"] == filters.match_id]
    if filters.confidence is not None:
        result = [v for v in result if v["confidence_tier"] == filters.confidence]

    # Section 10: a confirmed-out player is never shown in an active
    # consumer view, regardless of which lineup_filter tier was requested.
    result = [v for v in result if v.get("selection_status") != "confirmed_out"]

    if filters.lineup_filter == LINEUP_FILTER_CONFIRMED_ONLY:
        result = [v for v in result if v.get("is_confirmed")]
    elif filters.lineup_filter == LINEUP_FILTER_CONFIRMED_PLUS_EXPECTED:
        result = [v for v in result if v.get("is_confirmed") or v.get("selection_status") in EXPECTED_IN_SELECTION_STATUSES]
    # LINEUP_FILTER_INCLUDE_UNCERTAIN / None: no further filtering beyond the confirmed-out exclusion above.

    return result


def load_upcoming_disposal_projections(db: Session, filters: ProjectionFilters) -> list[dict]:
    rows = db.scalars(select(PlayerDisposalProjection)).all()
    current_version = current_disposal_model_version(db)
    current_team_version = compute_team_model_version(db)
    views = [_disposal_view(db, r, current_version, current_team_version) for r in rows]
    views = _apply_common_filters(views, filters)
    if filters.min_probability is not None:
        threshold = filters.probability_threshold or 20.0
        key = str(int(threshold)) if float(threshold).is_integer() else str(threshold)
        views = [v for v in views if key in v["thresholds"] and v["thresholds"][key]["probability"] >= filters.min_probability]
    views.sort(key=lambda v: v["expected"], reverse=True)
    return views


def load_upcoming_goal_projections(db: Session, filters: ProjectionFilters) -> list[dict]:
    rows = db.scalars(select(PlayerGoalProjection)).all()
    current_version = current_goal_model_version(db)
    current_team_version = compute_team_model_version(db)
    views = [_goal_view(db, r, current_version, current_team_version) for r in rows]
    views = _apply_common_filters(views, filters)
    if filters.min_probability is not None:
        threshold = filters.probability_threshold or 2.0
        key = str(int(threshold)) if float(threshold).is_integer() else str(threshold)
        views = [v for v in views if key in v["thresholds"] and v["thresholds"][key]["probability"] >= filters.min_probability]
    views.sort(key=lambda v: v["expected"], reverse=True)
    return views


def load_player_projection(db: Session, player_id: int) -> dict | None:
    disposal_row = db.scalar(select(PlayerDisposalProjection).where(PlayerDisposalProjection.player_id == player_id))
    goal_row = db.scalar(select(PlayerGoalProjection).where(PlayerGoalProjection.player_id == player_id))
    if disposal_row is None and goal_row is None:
        return None
    current_team_version = compute_team_model_version(db)
    return {
        "disposals": _disposal_view(db, disposal_row, current_disposal_model_version(db), current_team_version) if disposal_row else None,
        "goals": _goal_view(db, goal_row, current_goal_model_version(db), current_team_version) if goal_row else None,
    }


# --- Lineup summary (Sections 7-8) ------------------------------------------


def load_lineup_summary(db: Session, match_id: int) -> dict:
    rows = db.scalars(select(ExpectedLineup).where(ExpectedLineup.match_id == match_id)).all()
    counts = {s.value: 0 for s in SelectionStatus}
    for r in rows:
        counts[r.selection_status] = counts.get(r.selection_status, 0) + 1
    return {
        "match_id": match_id,
        "announcement_state": derive_announcement_state([r.selection_status for r in rows]),
        "n_confirmed_selected": counts.get(SelectionStatus.CONFIRMED_SELECTED.value, 0),
        "n_named_in_squad": counts.get(SelectionStatus.NAMED_IN_SQUAD.value, 0),
        "n_emergency": counts.get(SelectionStatus.EMERGENCY.value, 0),
        "n_substitute": counts.get(SelectionStatus.SUBSTITUTE.value, 0),
        "n_confirmed_out": counts.get(SelectionStatus.CONFIRMED_OUT.value, 0),
        "n_uncertain": counts.get(SelectionStatus.UNCERTAIN.value, 0),
        "n_placeholder": counts.get(SelectionStatus.PLACEHOLDER.value, 0),
        "n_manual_overrides": sum(1 for r in rows if r.is_manual_override),
        "last_updated": max((r.recorded_at for r in rows), default=None),
    }


# --- Historical calibration context (Section 18) ---------------------------
#
# "Model predictions in this range historically occurred at approximately
# X%" - sourced from the ALREADY-PERSISTED research-stage calibration
# metrics (disposal_persistence.py/goal_persistence.py's threshold_N
# segments), not recomputed here, and only ever shown for the fixed
# thresholds those research runs actually evaluated (64,282 eval games each
# - nowhere near a "tiny sample"). An arbitrary live threshold is mapped to
# its NEAREST evaluated threshold rather than interpolated, and labelled as
# such so it's clear the number describes a close reference point in the
# research report, not a fresh calculation for this exact line.

_DISPOSAL_CALIBRATION_THRESHOLDS = (20, 25, 30, 35)
_GOAL_CALIBRATION_THRESHOLDS = (1, 2, 3, 4, 5)
MIN_CALIBRATION_SAMPLE = 1000


@dataclass(frozen=True)
class CalibrationMetrics:
    """The numeric facts behind historical_calibration_note's pre-formatted
    string (Section 12 of the best-bets stage brief) - callers that want to
    render their own display (e.g. a per-threshold calibration table) or
    compare calibration quality across markets need the raw ECE/n/threshold,
    not just prose."""

    market_type: str
    requested_threshold: float
    evaluated_threshold: float
    ece: float
    n: int


def historical_calibration_metrics(db: Session, market_type: str, threshold: float) -> CalibrationMetrics | None:
    if market_type == PlayerMarket.DISPOSALS.value:
        run = db.scalar(select(PlayerModelRun).where(PlayerModelRun.is_promoted.is_(True)))
        candidates = _DISPOSAL_CALIBRATION_THRESHOLDS
        metric_cls = PlayerModelValidationMetric
    elif market_type == PlayerMarket.GOALS.value:
        run = db.scalar(select(GoalModelRun).where(GoalModelRun.is_promoted.is_(True)))
        candidates = _GOAL_CALIBRATION_THRESHOLDS
        metric_cls = GoalModelValidationMetric
    else:
        return None
    if run is None or not candidates:
        return None

    nearest = min(candidates, key=lambda t: abs(t - threshold))
    segment = f"threshold_{nearest}"
    ece_row = db.scalar(
        select(metric_cls).where(metric_cls.model_run_id == run.id, metric_cls.segment == segment, metric_cls.metric_name == "ece")
    )
    if ece_row is None or ece_row.n < MIN_CALIBRATION_SAMPLE:
        return None
    return CalibrationMetrics(
        market_type=market_type, requested_threshold=threshold, evaluated_threshold=float(nearest),
        ece=ece_row.value, n=ece_row.n,
    )


def historical_calibration_note(db: Session, market_type: str, threshold: float) -> str | None:
    metrics = historical_calibration_metrics(db, market_type, threshold)
    if metrics is None:
        return None
    return (
        f"Historical context: this model's {metrics.evaluated_threshold:g}+ predictions had a calibration error (ECE) of "
        f"{metrics.ece:.3f} across {metrics.n:,} evaluation games (nearest evaluated threshold to {metrics.requested_threshold:g})."
    )


# --- Prop insights (Sections 13-15, gating per Section 10) -----------------

_TIER_ORDER = ["insufficient_history", "lower_confidence", "moderate_confidence", "higher_confidence"]


def downgrade_confidence(tier: str) -> str:
    idx = _TIER_ORDER.index(tier) if tier in _TIER_ORDER else 0
    return _TIER_ORDER[max(idx - 1, 0)]


def load_prop_insights(
    db: Session, *, market: str | None = None, min_confidence: str | None = None, include_uncertain: bool = True
) -> list[dict]:
    quotes = db.scalars(select(PlayerPropMarket).order_by(PlayerPropMarket.recorded_at.desc())).all()
    if market is not None:
        quotes = [q for q in quotes if q.market_type == market]

    results = []
    for q in quotes:
        if q.market_type == PlayerMarket.DISPOSALS.value:
            proj = db.scalar(
                select(PlayerDisposalProjection).where(
                    PlayerDisposalProjection.match_id == q.match_id, PlayerDisposalProjection.player_id == q.player_id
                )
            )
            if proj is None:
                continue
            dist = disposal_distribution_for(proj)
            base_confidence_tier = proj.confidence_tier
            base_warnings = list(proj.warnings)
        elif q.market_type == PlayerMarket.GOALS.value:
            proj = db.scalar(
                select(PlayerGoalProjection).where(
                    PlayerGoalProjection.match_id == q.match_id, PlayerGoalProjection.player_id == q.player_id
                )
            )
            if proj is None:
                continue
            dist = goal_distribution_for(proj)
            base_confidence_tier = proj.confidence_tier
            base_warnings = list(proj.warnings)
        else:
            continue

        # Section 10: a confirmed-out player's prop is never shown in an
        # active consumer view at all - not just downgraded.
        current_lineup = current_lineup_for(db, q.player_id, q.match_id)
        selection_status = current_lineup.selection_status if current_lineup else "uncertain"
        is_confirmed = current_lineup.is_confirmed if current_lineup else False
        if selection_status == "confirmed_out":
            continue

        is_uncertain_participation = not is_confirmed and selection_status not in EXPECTED_IN_SELECTION_STATUSES
        if is_uncertain_participation and not include_uncertain:
            continue

        confidence_tier = downgrade_confidence(base_confidence_tier) if is_uncertain_participation else base_confidence_tier

        if min_confidence is not None and CONFIDENCE_RANK.get(confidence_tier, 0) < CONFIDENCE_RANK.get(min_confidence, 0):
            continue

        model_probability = price_line(dist, q.threshold, q.line_type)
        comparison = compare_model_to_market(model_probability, q.price_decimal)
        category = categorize_edge(comparison.difference_pp, confidence_tier)
        warning = rare_event_warning(q.market_type, q.threshold, model_probability)
        warnings = base_warnings + ([warning] if warning else [])
        if not comparison.overround_removed:
            warnings.append("Only one side of this market was quoted — the raw implied probability likely still includes bookmaker margin.")
        if is_uncertain_participation:
            warnings.append("This player's participation is not confirmed — confidence has been downgraded for this insight.")
        calibration_note = historical_calibration_note(db, q.market_type, q.threshold)
        if calibration_note:
            warnings.append(calibration_note)

        match = q.match
        results.append(
            {
                "id": q.id,
                "player_id": q.player_id,
                "player_name": q.player.display_name,
                "match_id": q.match_id,
                "round_number": match.round.round_number,
                "season_year": match.season.year,
                "bookmaker_name": q.bookmaker.name,
                "market_type": q.market_type,
                "line_type": q.line_type,
                "threshold": q.threshold,
                "recorded_at": q.recorded_at,
                "source": q.source,
                "model_probability": comparison.model_probability,
                "model_fair_odds": comparison.model_fair_odds,
                "offered_odds": comparison.offered_odds,
                "raw_implied_probability": comparison.raw_implied_probability,
                "devigged_probability": comparison.devigged_probability,
                "overround_removed": comparison.overround_removed,
                "difference_pp": comparison.difference_pp,
                "expected_value": comparison.expected_value,
                "edge_category": category,
                "confidence_tier": confidence_tier,
                "selection_status": selection_status,
                "is_confirmed": is_confirmed,
                "warnings": warnings,
            }
        )

    results.sort(key=lambda r: abs(r["difference_pp"]), reverse=True)
    return results


# --- Goal team-total diagnostic (Section 19, internal/Model Research only) -


def load_upcoming_goal_team_diagnostic(db: Session) -> list[dict]:
    upcoming = load_next_upcoming_round(db)
    if not upcoming:
        return []
    try:
        team_context = build_upcoming_team_context(db, upcoming)
    except Exception:
        return []

    goal_rows = db.scalars(
        select(PlayerGoalProjection).where(PlayerGoalProjection.match_id.in_([m.match_id for m in upcoming]))
    ).all()

    by_team: dict[tuple[int, int], float] = {}
    for r in goal_rows:
        by_team[(r.match_id, r.team_id)] = by_team.get((r.match_id, r.team_id), 0.0) + r.predicted_mean

    results = []
    for m in upcoming:
        for team_id in (m.home_team_id, m.away_team_id):
            ctx = team_context.get(m.match_id, {}).get(team_id, {})
            expected_score = ctx.get("expected_score")
            if expected_score is None:
                continue
            team_expected_goals = expected_score / 6.0
            sum_predicted = by_team.get((m.match_id, team_id), 0.0)
            results.append(
                {
                    "match_id": m.match_id,
                    "team_id": team_id,
                    "sum_predicted_goals": sum_predicted,
                    "team_expected_goals": team_expected_goals,
                    "gap": sum_predicted - team_expected_goals,
                }
            )
    return results
