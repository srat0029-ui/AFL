import enum
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Integer, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class MatchStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"


class Match(TimestampMixin, Base):
    __tablename__ = "matches"
    __table_args__ = (CheckConstraint("home_team_id != away_team_id", name="ck_match_distinct_teams"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sport_id: Mapped[int] = mapped_column(ForeignKey("sports.id"), nullable=False, index=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), nullable=False, index=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"), nullable=False, index=True)
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)
    venue_id: Mapped[int | None] = mapped_column(ForeignKey("venues.id"), nullable=True, index=True)

    scheduled_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[MatchStatus] = mapped_column(
        Enum(MatchStatus, native_enum=False, length=16), default=MatchStatus.SCHEDULED, nullable=False
    )

    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Goals (worth 6) and behinds (worth 1) are stored separately, not just
    # derived from home_score, because the scoring model (Stage 1.3) treats
    # them as two independent Poisson processes — total points alone doesn't
    # have Poisson-like variance and would produce overconfident probabilities.
    home_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_behinds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_behinds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_score_by_quarter: Mapped[list | None] = mapped_column(JSON, nullable=True)
    away_score_by_quarter: Mapped[list | None] = mapped_column(JSON, nullable=True)
    attendance: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # External identifiers this match is known by in upstream data sources, e.g.
    # {"squiggle": 12345}. Keeps ingestion idempotent without a source-specific column per provider.
    external_ids: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    season: Mapped["Season"] = relationship(back_populates="matches")
    round: Mapped["Round"] = relationship(back_populates="matches")
    venue: Mapped["Venue"] = relationship(back_populates="matches")
    home_team: Mapped["Team"] = relationship(back_populates="home_matches", foreign_keys=[home_team_id])
    away_team: Mapped["Team"] = relationship(back_populates="away_matches", foreign_keys=[away_team_id])
    odds_quotes: Mapped[list["OddsQuote"]] = relationship(back_populates="match")

    def __repr__(self) -> str:
        return f"<Match {self.home_team_id} v {self.away_team_id} @ {self.scheduled_start}>"
