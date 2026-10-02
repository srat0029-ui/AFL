"""One observation of a player's injury/availability state, as the source's
injury feed showed it. Append-only: a change is a NEW row, never an edit, so
if a player goes Day-To-Day -> Questionable -> Out all three rows remain,
each with the time it was first observed.

A row is written only when what the source shows for that player CHANGES
(any field: status, injury detail, comments, the source's own date). An
unchanged player produces no new row on later polls; that the state was
still being shown is carried by the poll history (NbaEvidencePoll). So a
state's interval is: first seen at this row's `observed_at`, superseded at
the player's next row, and last confirmed at the latest poll in between.

Two timestamps, deliberately distinct:

- `source_published_at`: the source's own date on the injury entry — when
  IT says the status was set or last updated.
- `observed_at`: when THIS system fetched the feed. The as-of boundary
  filters on this one. A status the source dated 4:30pm that we only
  fetched at 6pm was not information we held at 5pm.

Raw versus canonical status: `source_status` is exactly what the source
published. `status` is this project's canonical value and is NULL whenever
the source's term has no safe canonical meaning. ESPN's "Day-To-Day" is not
the league's questionable/doubtful/probable scale and is not mapped onto it.

Disappearing from the feed: the feed only lists players with an entry. When
a player who was listed is no longer there, a row is written with
`is_listed = False` and no status at all. That records the one thing that
is actually known — "the source stopped listing him at this time" — and
deliberately does NOT say he is healthy or available. A player with no rows
is simply one the feed has never listed while we were watching.

`team_id` is the team the feed listed the entry under. The feed also names a
team on the athlete record, and the two sometimes disagree; both raw ids are
kept (`source_team_id`, `source_athlete_team_id`).

`game_id` stays NULL for the league injury feed: it does not tie an entry to
a game, and no relationship is invented.

`raw` keeps the source's entry (minus image/link clutter) so a change to the
normalisation here can be re-derived from what was actually reported.
"""

import enum
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class NbaAvailabilityStatus(str, enum.Enum):
    """The league injury report's own vocabulary."""

    AVAILABLE = "available"
    PROBABLE = "probable"
    QUESTIONABLE = "questionable"
    DOUBTFUL = "doubtful"
    OUT = "out"


class NbaPlayerAvailabilityReport(TimestampMixin, Base):
    __tablename__ = "nba_player_availability_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    poll_id: Mapped[int | None] = mapped_column(ForeignKey("nba_evidence_polls.id"), nullable=True, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("nba_players.id"), nullable=False, index=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("nba_teams.id"), nullable=True, index=True)
    game_id: Mapped[int | None] = mapped_column(ForeignKey("nba_games.id"), nullable=True, index=True)

    is_listed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source_status: Mapped[str | None] = mapped_column(String(48), nullable=True)  # raw, as published; NULL only when not listed
    source_status_type: Mapped[str | None] = mapped_column(String(48), nullable=True)  # raw type name, e.g. "INJURY_STATUS_OUT"
    status: Mapped[str | None] = mapped_column(String(16), nullable=True)  # NbaAvailabilityStatus value; NULL = unmapped

    injury_type: Mapped[str | None] = mapped_column(String(64), nullable=True)  # e.g. "Knee"
    injury_location: Mapped[str | None] = mapped_column(String(64), nullable=True)  # e.g. "Leg"
    injury_side: Mapped[str | None] = mapped_column(String(32), nullable=True)
    injury_detail: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fantasy_status: Mapped[str | None] = mapped_column(String(16), nullable=True)  # raw abbreviation, e.g. "GTD"
    expected_return_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    short_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    long_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_report_id: Mapped[str | None] = mapped_column(String(32), nullable=True)  # the source's id for the injury entry
    source_team_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_athlete_team_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    player: Mapped["NbaPlayer"] = relationship(foreign_keys=[player_id])
    team: Mapped["NbaTeam | None"] = relationship(foreign_keys=[team_id])
    game: Mapped["NbaGame | None"] = relationship(foreign_keys=[game_id])

    def __repr__(self) -> str:
        state = repr(self.source_status) if self.is_listed else "not listed"
        return f"<NbaPlayerAvailabilityReport player={self.player_id} {state} observed={self.observed_at}>"
