"""Captures point-in-time history for `ModelValueObservation` (see that
model's docstring for why this table exists at all). Same idempotent
"insert a new row only when the value actually changed" discipline as
`app/player_modelling/team_odds_ingestion.py`'s OddsQuote writer: look up
the latest row for this exact identity, compare, and only write if it
genuinely moved.

Values are rounded before comparison (0.1pp for probabilities, 0.01 for
fair odds, 0.1 for a projected mean) — this is a capture-layer noise floor
only, not a materiality judgement. A rounded-away change is genuinely too
small to be a different observation; anything that survives rounding still
gets a row even if it's far below what `model_movement.py` would call
"material" — that filtering happens at analysis time, never here.

Reads only already-computed pricing (`team_pricing.price_team_market`,
already-persisted `PlayerDisposalProjection`/`PlayerGoalProjection` rows
via `live_report_query.py`'s distribution reconstruction) — never refits or
recomputes a model, matching every other pricing/snapshot module in this
codebase.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    KIND_FAIR_ODDS,
    KIND_PROBABILITY,
    KIND_PROJECTED_MEAN,
    Match,
    MatchStatus,
    ModelValueObservation,
    PlayerDisposalProjection,
    PlayerGoalProjection,
    VALUE_PLAYER_DISPOSAL_PROBABILITY,
    VALUE_PLAYER_DISPOSAL_PROJECTED_MEAN,
    VALUE_PLAYER_GOAL_PROBABILITY,
    VALUE_PLAYER_GOAL_PROJECTED_MEAN,
    VALUE_TEAM_FAIR_ODDS,
    VALUE_TEAM_WIN_PROBABILITY,
)
from app.player_modelling.live_report_query import disposal_distribution_for, goal_distribution_for
from app.pricing.player_pricing import DEFAULT_DISPOSAL_THRESHOLDS, DEFAULT_GOAL_THRESHOLDS, DISPOSAL_MODEL_NAME, GOAL_MODEL_NAME, _threshold_price

PROBABILITY_ROUND = 3  # 0.1pp
FAIR_ODDS_ROUND = 2
PROJECTED_MEAN_ROUND = 1


def _round_for_kind(value: float, kind: str) -> float:
    if kind == KIND_PROBABILITY:
        return round(value, PROBABILITY_ROUND)
    if kind == KIND_FAIR_ODDS:
        return round(value, FAIR_ODDS_ROUND)
    return round(value, PROJECTED_MEAN_ROUND)


def _latest(db: Session, *, match_id: int, player_id: int | None, value_type: str, selection: str | None, threshold: float | None) -> ModelValueObservation | None:
    return db.scalar(
        select(ModelValueObservation)
        .where(
            ModelValueObservation.match_id == match_id, ModelValueObservation.player_id == player_id,
            ModelValueObservation.value_type == value_type, ModelValueObservation.selection == selection,
            ModelValueObservation.threshold == threshold,
        )
        .order_by(ModelValueObservation.recorded_at.desc())
        .limit(1)
    )


def _record_if_changed(
    db: Session, *, match_id: int, player_id: int | None, value_type: str, value_kind: str, selection: str | None,
    threshold: float | None, value: float, lineup_status: str | None, model_name: str, model_version: str,
    data_cutoff: datetime | None, recorded_at: datetime,
) -> bool:
    rounded = _round_for_kind(value, value_kind)
    existing = _latest(db, match_id=match_id, player_id=player_id, value_type=value_type, selection=selection, threshold=threshold)
    if existing is not None and _round_for_kind(existing.value, value_kind) == rounded and existing.lineup_status == lineup_status:
        return False
    db.add(ModelValueObservation(
        match_id=match_id, player_id=player_id, value_type=value_type, value_kind=value_kind, selection=selection,
        threshold=threshold, value=rounded, lineup_status=lineup_status, model_name=model_name, model_version=model_version,
        data_cutoff=data_cutoff, recorded_at=recorded_at,
    ))
    return True


@dataclass
class ObservationReport:
    matches_considered: int = 0
    observations_created: int = 0


def record_model_value_observations(db: Session, match_ids: list[int]) -> ObservationReport:
    from app.edges.calculator import build_model_context
    from app.pricing.team_pricing import TEAM_MODEL_NAME, TEAM_MODEL_VERSION, latest_completed_match_timestamp, price_team_market

    report = ObservationReport()
    now = datetime.now(timezone.utc)
    context = build_model_context(db)
    team_data_cutoff = latest_completed_match_timestamp(db) or now

    for match_id in match_ids:
        match = db.get(Match, match_id)
        if match is None or match.status != MatchStatus.SCHEDULED:
            continue
        report.matches_considered += 1

        price = price_team_market(match, context, now, team_data_cutoff)
        for selection, prob, odds in (
            (match.home_team.name, price.home_win_probability, price.home_fair_odds),
            (match.away_team.name, price.away_win_probability, price.away_fair_odds),
        ):
            if _record_if_changed(
                db, match_id=match_id, player_id=None, value_type=VALUE_TEAM_WIN_PROBABILITY, value_kind=KIND_PROBABILITY,
                selection=selection, threshold=None, value=prob, lineup_status=None, model_name=TEAM_MODEL_NAME,
                model_version=TEAM_MODEL_VERSION, data_cutoff=team_data_cutoff, recorded_at=now,
            ):
                report.observations_created += 1
            if _record_if_changed(
                db, match_id=match_id, player_id=None, value_type=VALUE_TEAM_FAIR_ODDS, value_kind=KIND_FAIR_ODDS,
                selection=selection, threshold=None, value=odds, lineup_status=None, model_name=TEAM_MODEL_NAME,
                model_version=TEAM_MODEL_VERSION, data_cutoff=team_data_cutoff, recorded_at=now,
            ):
                report.observations_created += 1

        for row in db.scalars(select(PlayerDisposalProjection).where(PlayerDisposalProjection.match_id == match_id)).all():
            if _record_if_changed(
                db, match_id=match_id, player_id=row.player_id, value_type=VALUE_PLAYER_DISPOSAL_PROJECTED_MEAN,
                value_kind=KIND_PROJECTED_MEAN, selection=None, threshold=None, value=row.predicted_mean,
                lineup_status=row.lineup_status_at_generation, model_name=DISPOSAL_MODEL_NAME, model_version=row.model_version,
                data_cutoff=row.data_cutoff, recorded_at=now,
            ):
                report.observations_created += 1
            dist = disposal_distribution_for(row)
            for t in DEFAULT_DISPOSAL_THRESHOLDS:
                tp = _threshold_price(dist, t)
                if _record_if_changed(
                    db, match_id=match_id, player_id=row.player_id, value_type=VALUE_PLAYER_DISPOSAL_PROBABILITY,
                    value_kind=KIND_PROBABILITY, selection=None, threshold=t, value=tp.probability,
                    lineup_status=row.lineup_status_at_generation, model_name=DISPOSAL_MODEL_NAME, model_version=row.model_version,
                    data_cutoff=row.data_cutoff, recorded_at=now,
                ):
                    report.observations_created += 1

        for row in db.scalars(select(PlayerGoalProjection).where(PlayerGoalProjection.match_id == match_id)).all():
            if _record_if_changed(
                db, match_id=match_id, player_id=row.player_id, value_type=VALUE_PLAYER_GOAL_PROJECTED_MEAN,
                value_kind=KIND_PROJECTED_MEAN, selection=None, threshold=None, value=row.predicted_mean,
                lineup_status=row.lineup_status_at_generation, model_name=GOAL_MODEL_NAME, model_version=row.model_version,
                data_cutoff=row.data_cutoff, recorded_at=now,
            ):
                report.observations_created += 1
            dist = goal_distribution_for(row)
            for t in DEFAULT_GOAL_THRESHOLDS:
                tp = _threshold_price(dist, t)
                if _record_if_changed(
                    db, match_id=match_id, player_id=row.player_id, value_type=VALUE_PLAYER_GOAL_PROBABILITY,
                    value_kind=KIND_PROBABILITY, selection=None, threshold=t, value=tp.probability,
                    lineup_status=row.lineup_status_at_generation, model_name=GOAL_MODEL_NAME, model_version=row.model_version,
                    data_cutoff=row.data_cutoff, recorded_at=now,
                ):
                    report.observations_created += 1

    db.commit()
    return report
