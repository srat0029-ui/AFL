"""Prospective evidence: what was observable about teams and games BEFORE
they were played, recorded at the moment this system observed it.

Everything here can only be collected going forward — ESPN shows the
current state of a roster, a depth chart or a game's lineup, never what it
showed last week — so every row is an observation stamped with
`observed_at`, appended and never edited. A change in what the source shows
is a NEW row. That is what lets "what did we know at 3:17 PM" be answered
exactly: the latest row observed at or before 3:17 PM.

To keep that history compact without losing meaning, an observation is only
written when what the source shows CHANGES (compared by `content_hash`).
Between changes, the fact that the source was looked at again and still
showed the same thing is carried by NbaEvidencePoll: one row per successful
fetch. So for any instant the full answer is "this state, first seen then,
last confirmed at the most recent poll".

All of these tables are protected by app/core/prospective.py's write-once
guard (registered in app/models/nba/__init__.py): rows cannot be changed or
deleted through the ORM.

(Player availability lives in its own module, player_availability_report.py,
and follows the same rules.)
"""

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin

POLL_AVAILABILITY = "availability"
POLL_TEAM_ROSTER = "team_roster"
POLL_TEAM_DEPTH_CHART = "team_depth_chart"
POLL_GAME_LINEUP = "game_lineup"
POLL_SCHEDULE = "schedule"
# One per upcoming game returned by a successful live schedule fetch; scope is
# the game's source id and payload_sha256 its schedule_content_hash.
POLL_SCHEDULE_GAME = "schedule_game"
POLL_BOX_SCORES = "box_scores"

TEAM_OBSERVATION_ROSTER = "roster"
TEAM_OBSERVATION_DEPTH_CHART = "depth_chart"


class NbaEvidencePoll(TimestampMixin, Base):
    """One SUCCESSFUL fetch of one piece of evidence. A failed or rejected
    fetch leaves no row here (it is recorded on the live-cycle run instead),
    so the existence of a row always means "the source was read at this
    time and what it showed is reflected in the observation tables"."""

    __tablename__ = "nba_evidence_polls"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # POLL_* value
    # What was polled, when the kind is per-something: a source team id, or
    # "<source game id>:<source team id>". NULL for league-wide polls.
    scope: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    # The source's own timestamp for the response, when it supplies one.
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    items_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    observations_added: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:
        return f"<NbaEvidencePoll {self.kind} {self.scope or ''} at {self.observed_at}>"


class NbaTeamObservation(TimestampMixin, Base):
    """A team's listed roster, or its depth chart, as the source showed it.

    `payload` is a small normalised representation, not the raw response:
    - roster:      {"players": [{"id", "name", "position", "jersey", "status"}, ...]}
    - depth_chart: {"positions": {"pg": ["<player id>", ...in rank order], ...}}
    Player ids are the source's own. A depth chart is the source's editorial
    ordering - it is NOT a confirmed or announced starting lineup.
    """

    __tablename__ = "nba_team_observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    poll_id: Mapped[int] = mapped_column(ForeignKey("nba_evidence_polls.id"), nullable=False, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("nba_teams.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)  # TEAM_OBSERVATION_* value
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    team: Mapped["NbaTeam"] = relationship(foreign_keys=[team_id])

    def __repr__(self) -> str:
        return f"<NbaTeamObservation team={self.team_id} {self.kind} at {self.observed_at}>"


class NbaGameLineupObservation(TimestampMixin, Base):
    """One team's roster entry list for one game, as the source showed it at
    one moment — the evidence for whether (and how long before tip-off)
    starters and inactives become visible.

    Verified 2026-10-02: about 33 hours before tip-off the source lists the
    players but carries NO starter/active/did-not-play fields at all; after
    a game those fields are present. `has_starter_field` and
    `starters_flagged` record which of those two shapes was seen, and
    `tipoff_at_observation` / `game_status_at_observation` record how far
    from tip-off the observation was made, so the question "is this known
    before the game" is answered by collected rows rather than assumed.

    `payload`: {"entries": [{"id", "starter", "active", "did_not_play", "reason"}, ...]}
    with None wherever the source did not supply the field.
    """

    __tablename__ = "nba_game_lineup_observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    poll_id: Mapped[int] = mapped_column(ForeignKey("nba_evidence_polls.id"), nullable=False, index=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("nba_games.id"), nullable=False, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("nba_teams.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    tipoff_at_observation: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    game_status_at_observation: Mapped[str] = mapped_column(String(16), nullable=False)
    # The source's own "lineup available" flag for the game, when supplied.
    lineup_available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    has_starter_field: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    starters_flagged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    players_listed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    game: Mapped["NbaGame"] = relationship(foreign_keys=[game_id])
    team: Mapped["NbaTeam"] = relationship(foreign_keys=[team_id])

    def __repr__(self) -> str:
        return f"<NbaGameLineupObservation game={self.game_id} team={self.team_id} starters={self.starters_flagged} at {self.observed_at}>"


class NbaGameScheduleObservation(TimestampMixin, Base):
    """What a game's schedule looked like when observed: its status, tip-off
    time and listed date. `nba_games` is corrected in place and so only ever
    holds the CURRENT schedule; this table keeps each earlier version, so a
    postponement or a moved tip-off is history rather than an overwrite, and
    "when did we think this game tipped off, as of then" stays answerable.
    A row is written when a game is first observed and whenever any of the
    three values changes."""

    __tablename__ = "nba_game_schedule_observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("nba_games.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False)  # NbaGameStatus value
    source_status: Mapped[str | None] = mapped_column(String(48), nullable=True)  # raw
    scheduled_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    game_date: Mapped[date] = mapped_column(Date, nullable=False)

    game: Mapped["NbaGame"] = relationship(foreign_keys=[game_id])

    def __repr__(self) -> str:
        return f"<NbaGameScheduleObservation game={self.game_id} {self.status} tip={self.scheduled_start} at {self.observed_at}>"


RUN_IN_PROGRESS = "in_progress"
RUN_OK = "ok"
RUN_PARTIAL = "partial"  # a source request failed in at least one step; the others ran
RUN_FAILED = "failed"  # an internal error, or every step that was due failed
RUN_INTERRUPTED = "interrupted"  # found still in progress by a later run: the process died


class NbaLiveCycleRun(TimestampMixin, Base):
    """One invocation of the NBA live cycle. The row is committed BEFORE the
    first step runs and updated as steps finish, so a run that never reaches
    `finished_at` is evidence the process was killed, not silence.

    `steps`: [{"step", "status", "detail", "seconds"}, ...] - short
    human-readable details only, never credentials or raw payloads.
    Operational bookkeeping, not evidence: this row IS updated in place.
    """

    __tablename__ = "nba_live_cycle_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    steps: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    def __repr__(self) -> str:
        return f"<NbaLiveCycleRun {self.started_at} {self.status}>"
