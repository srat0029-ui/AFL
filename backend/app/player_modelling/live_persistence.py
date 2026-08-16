"""Upserts one live-projection run's results — the write side of
live_engine.py. Always replaces in place, one row per (match_id,
player_id) (see app/models/player_projection.py's docstring): a live
projection has no reason to keep old versions the way research-stage
model runs do, since Section 20 explicitly asks for idempotent,
version-aware re-generation, not an accumulating history.

Also removes any previously-persisted projection for a player who is no
longer in the current expected-player set for an upcoming match (e.g. their
lineup status flipped to EXPECTED_OUT, or their ExpectedLineup record was
deleted) — a stale "still projected" row for a player who is now marked out
would be actively misleading, not just outdated.
"""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import PlayerDisposalProjection, PlayerGoalProjection
from app.player_modelling.live_engine import LiveProjectionRun


def persist_projection_run(db: Session, run: LiveProjectionRun) -> tuple[int, int]:
    if not run.upcoming_matches:
        return (0, 0)

    match_ids = [m.match_id for m in run.upcoming_matches]
    current_disposal_keys = {(p.player_id, p.match_id) for p in run.disposal_projections}
    current_goal_keys = {(p.player_id, p.match_id) for p in run.goal_projections}

    existing_disposal = db.scalars(select(PlayerDisposalProjection).where(PlayerDisposalProjection.match_id.in_(match_ids))).all()
    for row in existing_disposal:
        if (row.player_id, row.match_id) not in current_disposal_keys:
            db.delete(row)

    existing_goal = db.scalars(select(PlayerGoalProjection).where(PlayerGoalProjection.match_id.in_(match_ids))).all()
    for row in existing_goal:
        if (row.player_id, row.match_id) not in current_goal_keys:
            db.delete(row)

    for p in run.disposal_projections:
        existing = db.scalar(
            select(PlayerDisposalProjection).where(
                PlayerDisposalProjection.match_id == p.match_id, PlayerDisposalProjection.player_id == p.player_id
            )
        )
        if existing is None:
            existing = PlayerDisposalProjection(match_id=p.match_id, player_id=p.player_id)
            db.add(existing)
        existing.team_id = p.team_id
        existing.model_name = run.disposal_run.model_name
        existing.model_version = run.disposal_model_version
        existing.generated_at = run.generated_at
        existing.data_cutoff = run.data_cutoff
        existing.lineup_status_at_generation = p.lineup_status
        existing.games_of_history = p.games_of_history
        existing.predicted_mean = p.predicted_mean
        existing.distribution_method = "nb"
        existing.nb_alpha = p.nb_alpha
        existing.confidence_tier = p.confidence_tier
        existing.warnings = p.warnings
        existing.input_features = p.input_features

    for p in run.goal_projections:
        existing = db.scalar(
            select(PlayerGoalProjection).where(PlayerGoalProjection.match_id == p.match_id, PlayerGoalProjection.player_id == p.player_id)
        )
        if existing is None:
            existing = PlayerGoalProjection(match_id=p.match_id, player_id=p.player_id)
            db.add(existing)
        existing.team_id = p.team_id
        existing.model_name = run.goal_run.model_name
        existing.model_version = run.goal_model_version
        existing.generated_at = run.generated_at
        existing.data_cutoff = run.data_cutoff
        existing.lineup_status_at_generation = p.lineup_status
        existing.games_of_history = p.games_of_history
        existing.predicted_mean = p.predicted_mean
        existing.distribution_kind = p.distribution_kind
        existing.nb_alpha = p.nb_alpha
        existing.p_score = p.p_score
        existing.mu_scored = p.mu_scored
        existing.alpha_scored = p.alpha_scored
        existing.scoring_archetype = p.scoring_archetype
        existing.confidence_tier = p.confidence_tier
        existing.warnings = p.warnings
        existing.input_features = p.input_features

    db.commit()
    return (len(run.disposal_projections), len(run.goal_projections))
