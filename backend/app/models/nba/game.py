"""One NBA game — schedule and result.

No `Round`: the NBA has no rounds, and forcing game days into AFL's Round
model would be a fiction. A season is identified by the calendar year it
STARTS in (season_start_year=2026 is the 2026-27 season) plus a season type.

Schedule-derived context — rest days, back-to-backs, home/away, opponent —
is deliberately not stored. It is computed from this table as of a
prediction's information cutoff, so a rescheduled game can never leave a
stale derived value behind.

`scheduled_start` is the tip-off instant in UTC and is the prospective
boundary for everything attached to the game: predictions must be frozen
before it, and the closing line is the last quote observed at or before it.
`game_date` is the LOCAL calendar date the league lists the game under (a
7:30pm tip in Los Angeles is already the next day in UTC), which is what
"back-to-back" is defined on.

Provenance: identity is (source, source_game_id) — the provider's own game
id, never the matchup or the date. `status` and `season_type` are this
project's normalised values; `source_status` and `source_season_type` keep
exactly what the provider said, so a normalisation that later turns out
wrong can be audited and redone without re-fetching. `source_synced_at` is
when the schedule row was last confirmed against the provider.

`box_score_state` / `box_score_synced_at` record the outcome of the last
box-score fetch for this game. They are what make a long backfill
resumable: a game whose box score was stored, or which the provider
published with no player rows, is not fetched again by default.
"""

import enum
from datetime import date, datetime

from sqlalchemy import JSON, CheckConstraint, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class NbaGameStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    FINAL = "final"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"


class NbaBoxScoreState(str, enum.Enum):
    INGESTED = "ingested"
    # Stored, but the source published at least one player row with no usable
    # player id. That player is absent; everyone else's line is as published
    # and the totals still reconcile once the unidentified row is counted.
    INGESTED_PARTIAL = "ingested_partial"
    NO_PLAYER_ROWS = "no_player_rows"  # provider reports the game final but published no player lines
    NOT_FINAL = "not_final"  # provider's box score did not report the game as final; nothing stored
    REJECTED = "rejected"  # player points do not add up to the team's score; nothing stored
    FAILED = "failed"  # the request or its parsing failed


class NbaSeasonType(str, enum.Enum):
    PRESEASON = "preseason"
    REGULAR = "regular"
    PLAY_IN = "play_in"
    PLAYOFFS = "playoffs"


# The modelling dataset: games that count. Preseason games are kept in the
# schedule, classified as such, but are exhibition basketball - rotations
# and minutes are not representative - and All-Star events are never stored
# at all (they are not between two franchises).
COMPETITIVE_SEASON_TYPES: tuple[str, ...] = (NbaSeasonType.REGULAR.value, NbaSeasonType.PLAY_IN.value, NbaSeasonType.PLAYOFFS.value)


class NbaGame(TimestampMixin, Base):
    __tablename__ = "nba_games"
    __table_args__ = (
        UniqueConstraint("source", "source_game_id", name="uq_nba_game_source_id"),
        CheckConstraint("home_team_id != away_team_id", name="ck_nba_game_distinct_teams"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    season_start_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    season_type: Mapped[str] = mapped_column(String(16), nullable=False, default=NbaSeasonType.REGULAR.value)

    game_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    scheduled_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=NbaGameStatus.SCHEDULED.value, index=True)

    home_team_id: Mapped[int] = mapped_column(ForeignKey("nba_teams.id"), nullable=False, index=True)
    away_team_id: Mapped[int] = mapped_column(ForeignKey("nba_teams.id"), nullable=False, index=True)
    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_game_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_status: Mapped[str | None] = mapped_column(String(48), nullable=True)  # raw, e.g. "STATUS_FINAL"
    source_season_type: Mapped[str | None] = mapped_column(String(48), nullable=True)  # raw, e.g. "2:regular-season"
    source_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    box_score_state: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)  # NbaBoxScoreState value
    box_score_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Other providers' ids for the same game, e.g. {"the_odds_api": "<event id>"}.
    external_ids: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    home_team: Mapped["NbaTeam"] = relationship(foreign_keys=[home_team_id])
    away_team: Mapped["NbaTeam"] = relationship(foreign_keys=[away_team_id])

    def __repr__(self) -> str:
        return f"<NbaGame {self.away_team_id} @ {self.home_team_id} {self.scheduled_start}>"
