"""One observation of a player's injury/availability status. Append-only:
a status change is a NEW row, never an edit, so "what did we know about
this player at 5pm" is always answerable from the table.

Two timestamps, deliberately distinct:

- `source_published_at`: when the source says it published the report
  (NULL when the source does not say).
- `observed_at`: when THIS system fetched it. This is the one the as-of
  boundary filters on — a report published at 4:30pm that we only fetched
  at 6pm was not information we held at 5pm, and must not feed a 5pm
  prediction.

`game_id` is NULL for a report not tied to a specific game (e.g. "out for
the season").

Raw versus canonical status: `source_status` is exactly what the source
published and is always stored. `status` is this project's canonical value
and is NULL whenever the source's term has no safe canonical meaning. ESPN's
"Day-To-Day", for example, is not the league's questionable/doubtful/
probable scale and is not mapped onto it by assumption — it is stored raw
with `status` left NULL.
"""

import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
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
    player_id: Mapped[int] = mapped_column(ForeignKey("nba_players.id"), nullable=False, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("nba_teams.id"), nullable=False, index=True)
    game_id: Mapped[int | None] = mapped_column(ForeignKey("nba_games.id"), nullable=True, index=True)

    source_status: Mapped[str] = mapped_column(String(48), nullable=False)  # raw, as published
    status: Mapped[str | None] = mapped_column(String(16), nullable=True)  # NbaAvailabilityStatus value; NULL = unmapped
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    player: Mapped["NbaPlayer"] = relationship(foreign_keys=[player_id])
    team: Mapped["NbaTeam"] = relationship(foreign_keys=[team_id])
    game: Mapped["NbaGame | None"] = relationship(foreign_keys=[game_id])

    def __repr__(self) -> str:
        return f"<NbaPlayerAvailabilityReport player={self.player_id} {self.source_status!r} observed={self.observed_at}>"
