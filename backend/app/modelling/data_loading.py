"""Shared DB-loading for walk-forward model replay — used by both the Elo
and Poisson CLIs so match-loading logic (and its filtering rules) exists in
exactly one place.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modelling.types import MatchResult
from app.models import Match, MatchStatus, Sport


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
