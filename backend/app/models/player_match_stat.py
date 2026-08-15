"""Raw, per-player, per-match statistics from an external stats source (see
app/providers/afl/afltables.py). Mirrors TeamMatchStat's design: stores the
source's raw fields (not derived rolling averages, which get recomputed as
needed from this table — see app/models/team_match_stat.py's docstring for
why), and leaves genuinely unavailable fields NULL rather than guessing.

team_id is the team the player represented IN THIS SPECIFIC MATCH, not
players.current_team_id — a player's team can and does change over their
career (trades, delistings, re-drafts), so this table is the source of
truth for "who did this player play for on this date," never derived from
the player's current team.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class PlayerMatchStat(TimestampMixin, Base):
    __tablename__ = "player_match_stats"
    __table_args__ = (
        UniqueConstraint("player_id", "match_id", "source", name="uq_player_match_stat_player_match_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False, index=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)
    opponent_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True, index=True)

    source: Mapped[str] = mapped_column(String(32), nullable=False)  # e.g. "afltables"
    # The source's per-player identifier for this row — redundant with
    # players.source_player_id but kept per-row for provenance/traceability
    # independent of whatever the player's row currently points to.
    source_player_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    jumper_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subbed_on: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    subbed_off: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Same field set as TeamMatchStat, at player granularity.
    kicks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    marks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    handballs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disposals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    behinds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hitouts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tackles: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rebound_50s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inside_50s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clearances: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clangers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frees_for: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frees_against: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # NULL for finals — the Brownlow Medal is a home-and-away-season-only award.
    brownlow_votes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    contested_possessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uncontested_possessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    contested_marks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    marks_inside_50: Mapped[int | None] = mapped_column(Integer, nullable=True)
    one_percenters: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bounces: Mapped[int | None] = mapped_column(Integer, nullable=True)
    goal_assists: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Whole-number percentage of the match the player was on ground for.
    time_on_ground_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Not published by AFL Tables at all (verified — no equivalent field on
    # either the match-page or team-game-by-game formats). Always NULL for
    # now; kept as a column so a future source or a standard-formula
    # derivation doesn't need a schema change to populate it.
    fantasy_points: Mapped[float | None] = mapped_column(Float, nullable=True)

    player: Mapped["Player"] = relationship(foreign_keys=[player_id])
    match: Mapped["Match"] = relationship(foreign_keys=[match_id])
    team: Mapped["Team"] = relationship(foreign_keys=[team_id])
    opponent_team: Mapped["Team | None"] = relationship(foreign_keys=[opponent_team_id])

    def __repr__(self) -> str:
        return f"<PlayerMatchStat player={self.player_id} match={self.match_id} source={self.source!r}>"
