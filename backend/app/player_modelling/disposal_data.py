"""Loads the raw, point-in-time-ordered dataset the disposal model is built
from: one row per (player, completed match) where that player actually has
a PlayerMatchStat row for that match.

Eligibility is deliberately defined this way, not derived from a squad list:
this stage answers "given the player is selected and plays, how many
disposals will they record?" — not "will they be selected?" (see
app/player_modelling/__init__.py and the disposal-prediction stage brief).
A player who did not feature in a match has no row here and is never a
prediction target for that match.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, MatchStatus, PlayerMatchStat, Sport, TeamMatchStat


@dataclass(frozen=True)
class PlayerGameRow:
    """One player's actual, fully-known record of a completed match — the
    raw material feature-building walks over. Nothing here is a leakage
    risk by itself; the leakage discipline lives entirely in
    disposal_features.py's rule that a row at index i may only be used to
    build features for rows AFTER i for the same player/team/venue."""

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
    disposals: int
    kicks: int | None
    handballs: int | None
    marks: int | None
    tackles: int | None
    clearances: int | None
    inside_50s: int | None
    contested_possessions: int | None
    uncontested_possessions: int | None
    time_on_ground_pct: int | None
    subbed_on: bool
    subbed_off: bool


@dataclass(frozen=True)
class TeamGameRow:
    """One team's actual record of a completed match, from TeamMatchStat —
    used for team/opponent disposal-environment context features, kept
    separate from PlayerGameRow since it's a different grain (one row per
    team-match, not per player-match)."""

    team_id: int
    opponent_team_id: int
    match_id: int
    season_year: int
    scheduled_start: datetime
    disposals: int | None


def load_player_game_rows(db: Session, sport_code: str = "AFL", source: str = "afltables") -> list[PlayerGameRow]:
    rows = db.execute(
        select(PlayerMatchStat, Match)
        .join(Match, Match.id == PlayerMatchStat.match_id)
        .join(Sport, Sport.id == Match.sport_id)
        .where(
            Sport.code == sport_code,
            Match.status == MatchStatus.COMPLETED,
            PlayerMatchStat.source == source,
            PlayerMatchStat.disposals.is_not(None),
        )
        .order_by(Match.scheduled_start, Match.id)
    ).all()

    result = []
    for stat, match in rows:
        is_home = stat.team_id == match.home_team_id
        result.append(
            PlayerGameRow(
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
                disposals=stat.disposals,
                kicks=stat.kicks,
                handballs=stat.handballs,
                marks=stat.marks,
                tackles=stat.tackles,
                clearances=stat.clearances,
                inside_50s=stat.inside_50s,
                contested_possessions=stat.contested_possessions,
                uncontested_possessions=stat.uncontested_possessions,
                time_on_ground_pct=stat.time_on_ground_pct,
                subbed_on=stat.subbed_on,
                subbed_off=stat.subbed_off,
            )
        )
    return result


def load_team_game_rows(db: Session, sport_code: str = "AFL", source: str = "afltables") -> list[TeamGameRow]:
    rows = db.execute(
        select(TeamMatchStat, Match)
        .join(Match, Match.id == TeamMatchStat.match_id)
        .join(Sport, Sport.id == Match.sport_id)
        .where(Sport.code == sport_code, Match.status == MatchStatus.COMPLETED, TeamMatchStat.source == source)
        .order_by(Match.scheduled_start, Match.id)
    ).all()
    return [
        TeamGameRow(
            team_id=stat.team_id,
            opponent_team_id=stat.opponent_team_id,
            match_id=match.id,
            season_year=match.season.year,
            scheduled_start=match.scheduled_start,
            disposals=stat.disposals,
        )
        for stat, match in rows
    ]
