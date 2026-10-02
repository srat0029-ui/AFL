"""One model's projection for one player, one game, one market, generated
from information available at one cutoff. Append-only and fully frozen.

This is a deliberate departure from AFL, where the live projection tables
are upserted in place and a separate table had to be added later to recover
model-value history. Here a re-projection after new information (an injury
report, a lineup change) is a NEW row with a later `information_cutoff`, so
"how did the model's belief move, and what did it know each time" is
answerable directly.

The row stores the model's decomposition, not just its answer:

    predicted_mean = expected_minutes x rate_per_minute

Both factors are nullable so a baseline that does not decompose (e.g. a
plain rolling average of points) can still be recorded and compared through
the same pipeline — but a model that does decompose records both, so
minutes error and rate error can be evaluated separately.

Distribution PARAMETERS are stored, not pre-computed line probabilities, so
any line a bookmaker posts can be priced from the row (same convention as
AFL's projection tables).

`inputs` holds the feature values the model actually consumed plus their
provenance (e.g. which availability report ids), for audit.
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class NbaPropProjection(TimestampMixin, Base):
    __tablename__ = "nba_prop_projections"
    __table_args__ = (
        UniqueConstraint(
            "game_id", "player_id", "market", "model_version", "information_cutoff",
            name="uq_nba_prop_projection_identity",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("nba_games.id"), nullable=False, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("nba_players.id"), nullable=False, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("nba_teams.id"), nullable=False, index=True)
    market: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # NbaPropMarket value

    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Nothing learned after this instant fed the projection. Always <= generated_at.
    information_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    expected_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    rate_per_minute: Mapped[float | None] = mapped_column(Float, nullable=True)
    predicted_mean: Mapped[float] = mapped_column(Float, nullable=False)
    distribution_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    distribution_params: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    games_of_history: Mapped[int] = mapped_column(Integer, nullable=False)
    inputs: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    game: Mapped["NbaGame"] = relationship(foreign_keys=[game_id])
    player: Mapped["NbaPlayer"] = relationship(foreign_keys=[player_id])
    team: Mapped["NbaTeam"] = relationship(foreign_keys=[team_id])

    def __repr__(self) -> str:
        return f"<NbaPropProjection player={self.player_id} game={self.game_id} {self.market} mean={self.predicted_mean:.1f}>"
