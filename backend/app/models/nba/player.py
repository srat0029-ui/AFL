"""A player's durable identity. Anchored to (source, source_player_id) — the
stats source's own stable id — never to the display name, which is neither
unique nor stable (same rule as AFL's Player, for the same reason).

current_team_id is a display convenience only. NBA players change teams
mid-season far more often than AFL players, so the team a player actually
represented in a given game is always read from NbaPlayerGameLog.team_id.
"""

from sqlalchemy import JSON, Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class NbaPlayer(TimestampMixin, Base):
    __tablename__ = "nba_players"
    __table_args__ = (UniqueConstraint("source", "source_player_id", name="uq_nba_player_source_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    current_team_id: Mapped[int | None] = mapped_column(ForeignKey("nba_teams.id"), nullable=True, index=True)
    position: Mapped[str | None] = mapped_column(String(8), nullable=True)

    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_player_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # NULL until there is evidence either way — never guessed.
    is_active: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    current_team: Mapped["NbaTeam | None"] = relationship(foreign_keys=[current_team_id])

    def __repr__(self) -> str:
        return f"<NbaPlayer {self.display_name!r} source={self.source}:{self.source_player_id}>"
