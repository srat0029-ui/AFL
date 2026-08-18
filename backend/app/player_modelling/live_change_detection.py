"""Detects which upcoming matches actually need their projections
regenerated — Section 5 of the team-selection stage brief. Avoids
recomputing a whole round's projections when nothing relevant has
changed, which matters once `refresh-live` is expected to be run
routinely (Sections 11-12).

A match needs regeneration if:
  - its set of currently-expected players (status in/uncertain) differs
    from who currently has a persisted projection at all (someone newly
    expected, or someone projected who's no longer expected), or
  - any currently-expected, already-projected player's projection is
    stale by live_staleness.check_staleness's own rules (lineup status,
    model version, or team-model version moved since generation).

Reuses check_staleness rather than re-deriving a second notion of
"changed" — "stale" and "needs regenerating" are the same condition.
"""

from datetime import timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExpectedLineup, ExpectedLineupStatus, MatchContextItem, PlayerDisposalProjection, PlayerGoalProjection
from app.player_modelling.live_engine import compute_team_model_version
from app.player_modelling.live_report_query import current_disposal_model_version, current_goal_model_version
from app.player_modelling.live_staleness import check_staleness
from app.player_modelling.upcoming_features import UpcomingMatchTeams

_ELIGIBLE_STATUSES = (ExpectedLineupStatus.EXPECTED_IN.value, ExpectedLineupStatus.UNCERTAIN.value)


def _latest_context_by_player(db: Session, match_ids: list[int]) -> dict[tuple[int, int], object]:
    """(player_id, match_id) -> latest source_timestamp/recorded_at across
    that player's current-context items — Section 7/13: a projection whose
    generation predates newer context should be regenerated."""
    items = db.scalars(
        select(MatchContextItem).where(MatchContextItem.match_id.in_(match_ids), MatchContextItem.player_id.is_not(None))
    ).all()
    latest: dict[tuple[int, int], object] = {}
    for item in items:
        ts = item.source_timestamp or item.recorded_at
        ts = ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)
        key = (item.player_id, item.match_id)
        if key not in latest or ts > latest[key]:
            latest[key] = ts
    return latest


def detect_matches_needing_regeneration(db: Session, upcoming_matches: list[UpcomingMatchTeams]) -> set[int]:
    if not upcoming_matches:
        return set()

    match_ids = [m.match_id for m in upcoming_matches]
    current_disposal_version = current_disposal_model_version(db)
    current_goal_version = current_goal_model_version(db)
    current_team_version = compute_team_model_version(db)

    lineups = db.scalars(
        select(ExpectedLineup).where(ExpectedLineup.match_id.in_(match_ids), ExpectedLineup.status.in_(_ELIGIBLE_STATUSES))
    ).all()
    expected_by_match: dict[int, set[int]] = {}
    lineup_status_by_key: dict[tuple[int, int], str] = {}
    for lu in lineups:
        expected_by_match.setdefault(lu.match_id, set()).add(lu.player_id)
        lineup_status_by_key[(lu.player_id, lu.match_id)] = lu.status

    disposal_rows = db.scalars(select(PlayerDisposalProjection).where(PlayerDisposalProjection.match_id.in_(match_ids))).all()
    goal_rows = db.scalars(select(PlayerGoalProjection).where(PlayerGoalProjection.match_id.in_(match_ids))).all()
    latest_context = _latest_context_by_player(db, match_ids)

    changed: set[int] = set()

    for m in upcoming_matches:
        expected_players = expected_by_match.get(m.match_id, set())
        projected_disposal = {r.player_id: r for r in disposal_rows if r.match_id == m.match_id}
        projected_goal = {r.player_id: r for r in goal_rows if r.match_id == m.match_id}

        if expected_players != set(projected_disposal.keys()) or expected_players != set(projected_goal.keys()):
            changed.add(m.match_id)
            continue

        for player_id in expected_players:
            current_status = lineup_status_by_key.get((player_id, m.match_id))
            player_latest_context = latest_context.get((player_id, m.match_id))
            d = projected_disposal.get(player_id)
            if d is not None and check_staleness(
                projection_model_version=d.model_version, projection_data_cutoff=d.data_cutoff,
                projection_lineup_status=d.lineup_status_at_generation, current_model_version=current_disposal_version,
                current_data_cutoff=None, current_lineup_status=current_status,
                projection_team_model_version=d.team_model_version, current_team_model_version=current_team_version,
                projection_generated_at=d.generated_at, latest_context_at=player_latest_context,
            ).is_stale:
                changed.add(m.match_id)
                break
            g = projected_goal.get(player_id)
            if g is not None and check_staleness(
                projection_model_version=g.model_version, projection_data_cutoff=g.data_cutoff,
                projection_lineup_status=g.lineup_status_at_generation, current_model_version=current_goal_version,
                current_data_cutoff=None, current_lineup_status=current_status,
                projection_team_model_version=g.team_model_version, current_team_model_version=current_team_version,
                projection_generated_at=g.generated_at, latest_context_at=player_latest_context,
            ).is_stale:
                changed.add(m.match_id)
                break

    return changed
