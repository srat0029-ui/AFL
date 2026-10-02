"""What an NBA stats source reports, as plain data — decoupled from the ORM
so a second source is a new provider producing these same shapes.

These are NBA-specific rather than reusing app/providers/types.py's Fixture
and PlayerStatLine: those require an AFL round number / round label, which
an NBA game does not have.

Status and season-type values are normalised to this project's own
vocabulary (NbaGameStatus / NbaSeasonType values) by the provider. The raw
value the source published travels alongside each normalised one
(`source_status`, `source_season_type`) and is stored, so a normalisation
can always be audited against what the source actually said.
"""

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class NbaTeamRecord:
    source: str
    source_team_id: str
    name: str  # e.g. "Boston Celtics"
    abbreviation: str  # e.g. "BOS"


@dataclass(frozen=True)
class NbaGameRecord:
    source: str
    source_game_id: str
    season_start_year: int  # 2026 for the 2026-27 season
    season_type: str  # NbaSeasonType value
    game_date: date  # the league's local calendar date for the game
    scheduled_start: datetime  # tip-off, UTC
    status: str  # NbaGameStatus value
    home_source_team_id: str
    away_source_team_id: str
    home_team_name: str
    away_team_name: str
    home_score: int | None = None
    away_score: int | None = None
    source_status: str | None = None  # raw, e.g. "STATUS_FINAL"
    source_season_type: str | None = None  # raw, e.g. "2:regular-season"


@dataclass(frozen=True)
class NbaPlayerBoxLine:
    source_player_id: str
    player_name: str
    position: str | None
    source_team_id: str
    did_not_play: bool
    did_not_play_reason: str | None = None
    started: bool | None = None
    minutes: float | None = None
    points: int | None = None
    rebounds: int | None = None
    assists: int | None = None
    offensive_rebounds: int | None = None
    defensive_rebounds: int | None = None
    field_goals_made: int | None = None
    field_goals_attempted: int | None = None
    three_pointers_made: int | None = None
    three_pointers_attempted: int | None = None
    free_throws_made: int | None = None
    free_throws_attempted: int | None = None
    steals: int | None = None
    blocks: int | None = None
    turnovers: int | None = None
    personal_fouls: int | None = None
    plus_minus: int | None = None


@dataclass(frozen=True)
class NbaBoxScore:
    source: str
    source_game_id: str
    status: str  # NbaGameStatus value, as reported by the box score itself
    fetched_at: datetime
    lines: list[NbaPlayerBoxLine]
    source_status: str | None = None  # raw
    # Each team's final score as the box score's own header states it, keyed
    # by the source's team id. Used to check the player rows add up.
    team_scores: dict[str, int] | None = None
    malformed_rows: int = 0  # player rows the source published without a usable player id
    # Points scored by those unidentifiable rows, per source team id. They
    # cannot be attributed to a player, but they must still be counted when
    # checking that a team's player points add up to its score.
    unattributed_points: dict[str, int] | None = None
