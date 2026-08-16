"""Manually-entered expected-lineup status for an upcoming match — the
mechanism the live player-projection stage needs since, unlike historical
backtesting (which knows exactly who played from PlayerMatchStat), an
upcoming match has no such record yet, and automated team-selection
scraping is explicitly out of scope for this stage.

One row per (match, player), upserted in place (not append-only history):
`recorded_at` marks when the status was last set, so a projection generated
from an old status can be told apart from a freshly-updated one (see
app/player_modelling/live_staleness.py). A player with no row here for an
upcoming match is simply not projected — the system never guesses that a
historical player is playing just because they played the previous match.
"""

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class ExpectedLineupStatus(str, enum.Enum):
    EXPECTED_IN = "expected_in"
    EXPECTED_OUT = "expected_out"
    UNCERTAIN = "uncertain"


class ExpectedLineup(TimestampMixin, Base):
    __tablename__ = "expected_lineups"
    __table_args__ = (UniqueConstraint("match_id", "player_id", name="uq_expected_lineup_match_player"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False)  # ExpectedLineupStatus value
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Optional extra context a manual entry can carry — all nullable, since
    # most entries will just be a plain status.
    substitute_risk: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    returning_from_injury: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    role_note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Percentage-point adjustment to the model's own TOG expectation (e.g.
    # -15.0 for "expected to play a reduced role") — NOT used to override the
    # historical TOG features fed into the model (those must stay leakage-safe
    # rolling history), only surfaced alongside a projection as manually
    # supplied context. Left for a later stage to actually wire into the
    # projection maths; stored now so the schema doesn't need to change when
    # that lands.
    expected_tog_adjustment: Mapped[float | None] = mapped_column(Float, nullable=True)

    match: Mapped["Match"] = relationship(foreign_keys=[match_id])
    player: Mapped["Player"] = relationship(foreign_keys=[player_id])
    team: Mapped["Team"] = relationship(foreign_keys=[team_id])

    def __repr__(self) -> str:
        return f"<ExpectedLineup match={self.match_id} player={self.player_id} status={self.status}>"
