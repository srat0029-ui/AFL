"""Loads the raw, point-in-time-ordered dataset the goal model is built
from — same eligibility rule as disposal_data.py (one row per player per
completed match they actually played in; see that module's docstring for
why "given selected and played" is this stage's scope, not team-selection
prediction), but with a different field set: goals is the target, and
scoring-relevant fields (behinds, marks_inside_50, goal_assists) that the
disposal model has no use for are included here instead.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, MatchStatus, PlayerMatchStat, Sport, TeamMatchStat


@dataclass(frozen=True)
class PlayerGoalGameRow:
    player_id: int
    match_id: int
    team_id: int
    opponent_team_id: int
    season_year: int
    round_number: int
    is_final: bool
    is_home: bool
    venue_id: int | None
    scheduled_start: datetime
    goals: int
    behinds: int | None
    disposals: int | None
    kicks: int | None
    marks: int | None
    handballs: int | None
    tackles: int | None
    contested_possessions: int | None
    uncontested_possessions: int | None
    inside_50s: int | None
    marks_inside_50: int | None
    goal_assists: int | None
    time_on_ground_pct: int | None
    subbed_on: bool
    subbed_off: bool


@dataclass(frozen=True)
class TeamGoalGameRow:
    """Team-level scoring context - deliberately separate from
    disposal_data.TeamGameRow's `disposals` grain, since goal features need
    goals/behinds/inside_50s at team level, not disposals."""

    team_id: int
    opponent_team_id: int
    match_id: int
    season_year: int
    scheduled_start: datetime
    goals: int | None
    behinds: int | None
    inside_50s: int | None


def load_player_goal_game_rows(db: Session, sport_code: str = "AFL", source: str = "afltables") -> list[PlayerGoalGameRow]:
    rows = db.execute(
        select(PlayerMatchStat, Match)
        .join(Match, Match.id == PlayerMatchStat.match_id)
        .join(Sport, Sport.id == Match.sport_id)
        .where(
            Sport.code == sport_code,
            Match.status == MatchStatus.COMPLETED,
            PlayerMatchStat.source == source,
            PlayerMatchStat.goals.is_not(None),
        )
        .order_by(Match.scheduled_start, Match.id)
    ).all()

    result = []
    for stat, match in rows:
        is_home = stat.team_id == match.home_team_id
        result.append(
            PlayerGoalGameRow(
                player_id=stat.player_id,
                match_id=match.id,
                team_id=stat.team_id,
                opponent_team_id=stat.opponent_team_id,
                season_year=match.season.year,
                round_number=match.round.round_number,
                is_final=match.round.name is not None,
                is_home=is_home,
                venue_id=match.venue_id,
                scheduled_start=match.scheduled_start,
                goals=stat.goals,
                behinds=stat.behinds,
                disposals=stat.disposals,
                kicks=stat.kicks,
                marks=stat.marks,
                handballs=stat.handballs,
                tackles=stat.tackles,
                contested_possessions=stat.contested_possessions,
                uncontested_possessions=stat.uncontested_possessions,
                inside_50s=stat.inside_50s,
                marks_inside_50=stat.marks_inside_50,
                goal_assists=stat.goal_assists,
                time_on_ground_pct=stat.time_on_ground_pct,
                subbed_on=stat.subbed_on,
                subbed_off=stat.subbed_off,
            )
        )
    return result


def load_team_goal_game_rows(db: Session, sport_code: str = "AFL", source: str = "afltables") -> list[TeamGoalGameRow]:
    rows = db.execute(
        select(TeamMatchStat, Match)
        .join(Match, Match.id == TeamMatchStat.match_id)
        .join(Sport, Sport.id == Match.sport_id)
        .where(Sport.code == sport_code, Match.status == MatchStatus.COMPLETED, TeamMatchStat.source == source)
        .order_by(Match.scheduled_start, Match.id)
    ).all()
    return [
        TeamGoalGameRow(
            team_id=stat.team_id,
            opponent_team_id=stat.opponent_team_id,
            match_id=match.id,
            season_year=match.season.year,
            scheduled_start=match.scheduled_start,
            goals=stat.goals,
            behinds=stat.behinds,
            inside_50s=stat.inside_50s,
        )
        for stat, match in rows
    ]
