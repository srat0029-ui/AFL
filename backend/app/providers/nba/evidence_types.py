"""What an NBA source reports about the present — injuries, rosters, depth
charts, a game's listed lineup — as plain data. Companion to types.py, which
covers schedule and results.

Every shape carries `fetched_at` (when the response was received: this is
the observation time) and, where the source supplies one, its own timestamp.
Nothing here is normalised beyond pulling fields out of the response:
statuses are the source's raw strings, and a field the source did not send
is None rather than a default.
"""

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class NbaInjuryItem:
    source_player_id: str
    player_name: str
    position: str | None
    source_team_id: str  # the team the feed lists this entry under
    source_athlete_team_id: str | None  # the team on the athlete record; can differ
    source_report_id: str | None
    source_status: str  # raw, e.g. "Day-To-Day"
    source_status_type: str | None  # raw, e.g. "INJURY_STATUS_DAYTODAY"
    source_published_at: datetime | None  # the source's own date on the entry
    injury_type: str | None
    injury_location: str | None
    injury_side: str | None
    injury_detail: str | None
    fantasy_status: str | None
    expected_return_date: date | None
    short_comment: str | None
    long_comment: str | None
    raw: dict  # the source's entry, minus image/link clutter


@dataclass(frozen=True)
class NbaInjuryFeed:
    source: str
    fetched_at: datetime
    source_timestamp: datetime | None  # when the source says it generated the feed
    payload_sha256: str
    items: list[NbaInjuryItem]
    teams_listed: int  # team blocks present in the feed (teams with no entries are omitted by the source)
    unresolvable_items: int = 0  # entries with no recoverable player id; cannot be attached to anyone


@dataclass(frozen=True)
class NbaRosterPlayer:
    source_player_id: str
    name: str
    position: str | None
    jersey: str | None
    status: str | None  # the source's roster status name, e.g. "Active"


@dataclass(frozen=True)
class NbaTeamRoster:
    source: str
    source_team_id: str
    fetched_at: datetime
    source_timestamp: datetime | None
    players: list[NbaRosterPlayer]


@dataclass(frozen=True)
class NbaDepthChart:
    source: str
    source_team_id: str
    fetched_at: datetime
    # position key -> source player ids in the source's rank order
    positions: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class NbaLineupEntry:
    source_player_id: str
    starter: bool | None  # None = the source did not send the field at all
    active: bool | None
    did_not_play: bool | None
    reason: str | None


@dataclass(frozen=True)
class NbaGameLineup:
    source: str
    source_game_id: str
    source_team_id: str
    fetched_at: datetime
    lineup_available: bool | None  # the source's own flag on the game
    has_starter_field: bool  # whether ANY entry carried a starter field
    entries: list[NbaLineupEntry]
