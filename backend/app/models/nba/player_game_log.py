"""One player's box score for one game — the raw record that both feeds the
model (as history) and settles props (as the outcome).

Minutes and starter status are first-class because the intended NBA model
is `expected minutes x expected production per minute`: minutes is a
modelled quantity in its own right, not just another stat column.

`team_id` is the team the player represented IN THIS GAME (see NbaPlayer).

A player who was on the roster but did not play has a row with
did_not_play=True and NULL stats — distinct from having no row at all,
which means "box score not ingested yet". Settlement depends on that
distinction (void vs still pending).

Unlike quotes and predictions this table is corrected in place: the league
issues stat corrections, and the latest official box score is the truth. A
prediction's settlement copies the value it settled against, so a later
correction never silently rewrites a settled result.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class NbaPlayerGameLog(TimestampMixin, Base):
    __tablename__ = "nba_player_game_logs"
    __table_args__ = (UniqueConstraint("player_id", "game_id", "source", name="uq_nba_player_game_log_player_game_source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("nba_players.id"), nullable=False, index=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("nba_games.id"), nullable=False, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("nba_teams.id"), nullable=False, index=True)
    opponent_team_id: Mapped[int] = mapped_column(ForeignKey("nba_teams.id"), nullable=False, index=True)
    is_home: Mapped[bool] = mapped_column(Boolean, nullable=False)

    source: Mapped[str] = mapped_column(String(32), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    did_not_play: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    did_not_play_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    minutes: Mapped[float | None] = mapped_column(Float, nullable=True)

    points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rebounds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assists: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Opportunity/usage inputs for the per-minute rate models. NULL when the
    # source does not publish them.
    offensive_rebounds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    defensive_rebounds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    field_goals_made: Mapped[int | None] = mapped_column(Integer, nullable=True)
    field_goals_attempted: Mapped[int | None] = mapped_column(Integer, nullable=True)
    three_pointers_made: Mapped[int | None] = mapped_column(Integer, nullable=True)
    three_pointers_attempted: Mapped[int | None] = mapped_column(Integer, nullable=True)
    free_throws_made: Mapped[int | None] = mapped_column(Integer, nullable=True)
    free_throws_attempted: Mapped[int | None] = mapped_column(Integer, nullable=True)
    steals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    blocks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    turnovers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    personal_fouls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plus_minus: Mapped[int | None] = mapped_column(Integer, nullable=True)

    player: Mapped["NbaPlayer"] = relationship(foreign_keys=[player_id])
    game: Mapped["NbaGame"] = relationship(foreign_keys=[game_id])
    team: Mapped["NbaTeam"] = relationship(foreign_keys=[team_id])
    opponent_team: Mapped["NbaTeam"] = relationship(foreign_keys=[opponent_team_id])

    def __repr__(self) -> str:
        return f"<NbaPlayerGameLog player={self.player_id} game={self.game_id} min={self.minutes}>"
