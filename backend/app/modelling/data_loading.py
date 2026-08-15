"""Shared DB-loading for walk-forward model replay — used by both the Elo
and Poisson CLIs so match-loading logic (and its filtering rules) exists in
exactly one place.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modelling.features import MatchFeatureInput
from app.modelling.types import MatchResult
from app.models import Match, MatchStatus, Sport, TeamMatchStat


def load_completed_matches(db: Session, sport_code: str = "AFL") -> list[MatchResult]:
    matches = db.scalars(
        select(Match)
        .join(Sport)
        .where(Sport.code == sport_code, Match.status == MatchStatus.COMPLETED)
        .order_by(Match.scheduled_start)
    ).all()
    return [
        MatchResult(
            match_id=m.id,
            season_year=m.season.year,
            scheduled_start=m.scheduled_start,
            home_team_id=m.home_team_id,
            away_team_id=m.away_team_id,
            home_score=m.home_score,
            away_score=m.away_score,
            home_goals=m.home_goals,
            home_behinds=m.home_behinds,
            away_goals=m.away_goals,
            away_behinds=m.away_behinds,
        )
        for m in matches
        if m.home_score is not None and m.away_score is not None
    ]


def load_matches_with_team_stats(
    db: Session, sport_code: str = "AFL", source: str = "afltables"
) -> list[MatchFeatureInput]:
    """Like load_completed_matches, but also joins each match's TeamMatchStat
    rows (home + away, from `source`) so app/modelling/features.py has the
    advanced-stat fields available. A match with no stats coverage for
    either side still comes back — with those fields left None — rather
    than being silently dropped; app/modelling/features.py's minimum-history
    rules handle the resulting gaps."""
    matches = db.scalars(
        select(Match)
        .join(Sport)
        .where(Sport.code == sport_code, Match.status == MatchStatus.COMPLETED)
        .order_by(Match.scheduled_start)
    ).all()
    match_ids = [m.id for m in matches]
    if not match_ids:
        return []

    stat_rows = db.scalars(
        select(TeamMatchStat).where(TeamMatchStat.match_id.in_(match_ids), TeamMatchStat.source == source)
    ).all()
    stats_by_match_team: dict[tuple[int, int], TeamMatchStat] = {(s.match_id, s.team_id): s for s in stat_rows}

    results = []
    for m in matches:
        if m.home_score is None or m.away_score is None:
            continue
        home_stat = stats_by_match_team.get((m.id, m.home_team_id))
        away_stat = stats_by_match_team.get((m.id, m.away_team_id))
        results.append(
            MatchFeatureInput(
                match_id=m.id,
                season_year=m.season.year,
                scheduled_start=m.scheduled_start,
                home_team_id=m.home_team_id,
                away_team_id=m.away_team_id,
                home_score=m.home_score,
                away_score=m.away_score,
                home_goals=m.home_goals,
                home_behinds=m.home_behinds,
                away_goals=m.away_goals,
                away_behinds=m.away_behinds,
                home_clearances=home_stat.clearances if home_stat else None,
                away_clearances=away_stat.clearances if away_stat else None,
                home_inside_50s=home_stat.inside_50s if home_stat else None,
                away_inside_50s=away_stat.inside_50s if away_stat else None,
                home_contested_possessions=home_stat.contested_possessions if home_stat else None,
                away_contested_possessions=away_stat.contested_possessions if away_stat else None,
                home_tackles=home_stat.tackles if home_stat else None,
                away_tackles=away_stat.tackles if away_stat else None,
                home_marks_inside_50=home_stat.marks_inside_50 if home_stat else None,
                away_marks_inside_50=away_stat.marks_inside_50 if away_stat else None,
            )
        )
    return results
