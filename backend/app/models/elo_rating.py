"""Per-match Elo rating snapshots.

One row per (match, team): the team's rating immediately before this match
(rating_before — what a prediction for this match must use to avoid
leakage) and immediately after (rating_after — the input to the next match
this team plays). `sequence` is the chronological processing order used by
the walk-forward backtester, so ordering never depends on comparing
datetimes (SQLite doesn't reliably preserve tzinfo across a round trip —
see app/ingestion/fixtures.py for the same issue elsewhere).

Recomputed wholesale each time the modelling CLI runs (see
app/modelling/elo_persistence.py) rather than versioned — this table always
reflects the current best Elo configuration, not a history of past configs.
"""

from sqlalchemy import Float, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class EloRating(TimestampMixin, Base):
    __tablename__ = "elo_ratings"
    __table_args__ = (UniqueConstraint("match_id", "team_id", name="uq_elo_rating_match_team"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    rating_before: Mapped[float] = mapped_column(Float, nullable=False)
    rating_after: Mapped[float] = mapped_column(Float, nullable=False)

    match: Mapped["Match"] = relationship()
    team: Mapped["Team"] = relationship()

    def __repr__(self) -> str:
        return f"<EloRating team={self.team_id} match={self.match_id} rating={self.rating_after:.1f}>"
