"""Orchestrates one full live-projection run — Section 3 of the
live-projection brief: identify the next upcoming round, identify expected
players (from manually-entered ExpectedLineup records — see
upcoming_features.py), build point-in-time features "as of now," load
whichever disposal/goal model is currently promoted, generate a full
projection for every expected player, and hand back plain result objects
for the CLI to report and live_persistence.py to upsert. Nothing here
touches the database for writes — this module only reads and computes.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import GoalModelRun, PlayerModelRun
from app.player_modelling.disposal_backtest import build_dataset as build_disposal_dataset
from app.player_modelling.goal_backtest import build_goal_dataset
from app.player_modelling.live_confidence import live_disposal_confidence, live_goal_confidence
from app.player_modelling.live_models import fit_live_disposal_model, fit_live_goal_model
from app.player_modelling.upcoming_features import (
    ExpectedPlayer,
    UpcomingMatchTeams,
    build_upcoming_disposal_features,
    build_upcoming_goal_features,
    load_expected_players,
    load_next_upcoming_round,
)
from app.player_modelling.upcoming_team_context import ModelsUnavailableError, build_upcoming_team_context

__all__ = [
    "ModelsUnavailableError",
    "PromotedModelsUnavailableError",
    "DisposalProjectionResult",
    "GoalProjectionResult",
    "LiveProjectionRun",
    "generate_live_projections",
]


class PromotedModelsUnavailableError(Exception):
    """Raised when no promoted disposal/goal model run has been persisted
    yet — run `python -m app.player_modelling.disposal_cli` and
    `python -m app.player_modelling.goal_cli` first."""


@dataclass(frozen=True)
class DisposalProjectionResult:
    player_id: int
    match_id: int
    team_id: int
    lineup_status: str
    games_of_history: int
    predicted_mean: float
    nb_alpha: float
    confidence_tier: str
    warnings: list[str]
    input_features: dict


@dataclass(frozen=True)
class GoalProjectionResult:
    player_id: int
    match_id: int
    team_id: int
    lineup_status: str
    games_of_history: int
    predicted_mean: float
    distribution_kind: str
    nb_alpha: float | None
    p_score: float | None
    mu_scored: float | None
    alpha_scored: float | None
    scoring_archetype: str
    confidence_tier: str
    warnings: list[str]
    input_features: dict


@dataclass(frozen=True)
class LiveProjectionRun:
    upcoming_matches: list[UpcomingMatchTeams]
    expected_players: list[ExpectedPlayer]
    disposal_run: PlayerModelRun | None
    goal_run: GoalModelRun | None
    disposal_model_version: str | None
    goal_model_version: str | None
    data_cutoff: datetime | None
    generated_at: datetime
    disposal_projections: list[DisposalProjectionResult]
    goal_projections: list[GoalProjectionResult]


def _load_promoted_runs(db: Session) -> tuple[PlayerModelRun, GoalModelRun]:
    disposal_run = db.scalar(select(PlayerModelRun).where(PlayerModelRun.is_promoted.is_(True)))
    goal_run = db.scalar(select(GoalModelRun).where(GoalModelRun.is_promoted.is_(True)))
    if disposal_run is None or goal_run is None:
        missing = []
        if disposal_run is None:
            missing.append("disposal (run `python -m app.player_modelling.disposal_cli`)")
        if goal_run is None:
            missing.append("goal (run `python -m app.player_modelling.goal_cli`)")
        raise PromotedModelsUnavailableError(f"No promoted model found for: {'; '.join(missing)}")
    return disposal_run, goal_run


def generate_live_projections(db: Session) -> LiveProjectionRun:
    now = datetime.now(timezone.utc)
    upcoming_matches = load_next_upcoming_round(db)
    if not upcoming_matches:
        return LiveProjectionRun(
            upcoming_matches=[], expected_players=[], disposal_run=None, goal_run=None,
            disposal_model_version=None, goal_model_version=None, data_cutoff=None,
            generated_at=now, disposal_projections=[], goal_projections=[],
        )

    match_ids = [m.match_id for m in upcoming_matches]
    expected_players = load_expected_players(db, match_ids)
    disposal_run, goal_run = _load_promoted_runs(db)

    # Raises ModelsUnavailableError if elo_cli/poisson_cli haven't been run
    # yet — deliberately left to propagate to the caller (see cli.py),
    # since a live projection without team context would be silently
    # missing a real, validated input rather than failing loudly.
    upcoming_team_context = build_upcoming_team_context(db, upcoming_matches)

    disposal_feature_by_key = build_upcoming_disposal_features(db, upcoming_matches, expected_players, upcoming_team_context)
    goal_feature_by_key = build_upcoming_goal_features(db, upcoming_matches, expected_players, upcoming_team_context)

    disposal_train_rows = build_disposal_dataset(db).all_rows
    goal_train_rows = build_goal_dataset(db).all_rows
    data_cutoff = max((r.scheduled_start for r in disposal_train_rows), default=now)

    live_disposal_model = fit_live_disposal_model(disposal_train_rows, disposal_run)
    live_goal_model = fit_live_goal_model(goal_train_rows, goal_run)

    disposal_model_version = f"{disposal_run.model_name}@{disposal_run.run_at.isoformat()}"
    goal_model_version = f"{goal_run.model_name}@{goal_run.run_at.isoformat()}"

    lineup_by_key = {(ep.player_id, ep.match_id): ep for ep in expected_players}

    disposal_rows_ordered = [disposal_feature_by_key[k] for k in disposal_feature_by_key]
    disposal_means = live_disposal_model.predict_many(disposal_rows_ordered) if disposal_rows_ordered else []
    disposal_projections: list[DisposalProjectionResult] = []
    for row, mean in zip(disposal_rows_ordered, disposal_means):
        ep = lineup_by_key[(row.player_id, row.match_id)]
        conf = live_disposal_confidence(
            games_of_history=row.games_of_history,
            tog_last5_avg=row.features.get("tog_last5_avg"),
            disposals_last5_std=row.features.get("disposals_last5_std"),
            lineup_status=ep.status,
        )
        disposal_projections.append(
            DisposalProjectionResult(
                player_id=row.player_id, match_id=row.match_id, team_id=row.team_id, lineup_status=ep.status,
                games_of_history=row.games_of_history, predicted_mean=max(float(mean), 0.0), nb_alpha=live_disposal_model.nb_alpha,
                confidence_tier=conf.tier, warnings=conf.warnings, input_features=dict(row.features),
            )
        )

    goal_rows_ordered = [goal_feature_by_key[k] for k in goal_feature_by_key]
    goal_means = live_goal_model.predict_mean(goal_rows_ordered) if goal_rows_ordered else []
    p_scores, mu_scoreds = (None, None)
    if goal_rows_ordered and live_goal_model.distribution_kind == "hurdle":
        p_scores, mu_scoreds = live_goal_model.predict_hurdle_params(goal_rows_ordered)

    goal_projections: list[GoalProjectionResult] = []
    for i, row in enumerate(goal_rows_ordered):
        ep = lineup_by_key[(row.player_id, row.match_id)]
        conf = live_goal_confidence(
            games_of_history=row.games_of_history,
            tog_last5_avg=row.features.get("tog_last5_avg"),
            goals_last5_std=row.features.get("goals_last5_std"),
            goals_career_avg=row.features.get("goals_career_avg"),
            lineup_status=ep.status,
        )
        goal_projections.append(
            GoalProjectionResult(
                player_id=row.player_id, match_id=row.match_id, team_id=row.team_id, lineup_status=ep.status,
                games_of_history=row.games_of_history, predicted_mean=max(float(goal_means[i]), 0.0),
                distribution_kind=live_goal_model.distribution_kind,
                nb_alpha=live_goal_model.nb_alpha if live_goal_model.distribution_kind == "nb" else None,
                p_score=float(p_scores[i]) if p_scores is not None else None,
                mu_scored=float(mu_scoreds[i]) if mu_scoreds is not None else None,
                alpha_scored=live_goal_model.alpha_scored if live_goal_model.distribution_kind == "hurdle" else None,
                scoring_archetype=conf.scoring_archetype, confidence_tier=conf.tier, warnings=conf.warnings,
                input_features=dict(row.features),
            )
        )

    return LiveProjectionRun(
        upcoming_matches=upcoming_matches, expected_players=expected_players, disposal_run=disposal_run, goal_run=goal_run,
        disposal_model_version=disposal_model_version, goal_model_version=goal_model_version, data_cutoff=data_cutoff,
        generated_at=now, disposal_projections=disposal_projections, goal_projections=goal_projections,
    )
