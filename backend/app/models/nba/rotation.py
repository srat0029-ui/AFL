"""Frozen pregame rotation outlooks (V1.5) - one row per player per upcoming
game per information cutoff. Append-only (protected in
app/models/nba/__init__.py).

The learned quantities are stored side by side and never combined:
p_play, p_rotation, expected_minutes_if_plays (V1), and
reconciled_minutes_if_plays (only when the reconciliation was adopted).
availability_evidence is the RAW injury-feed state known at the cutoff, and
experimental_rules holds separately labelled prospective rules that do not
alter the learned fields. Outcomes are not stored here; they are joined
from nba_player_game_logs once a game is final.
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class NbaRotationPrediction(TimestampMixin, Base):
    __tablename__ = "nba_rotation_predictions"
    __table_args__ = (
        UniqueConstraint("game_id", "player_id", "participation_model_run_id", "information_cutoff", name="uq_nba_rotation_prediction_identity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("nba_games.id"), nullable=False, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("nba_players.id"), nullable=False, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("nba_teams.id"), nullable=False, index=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    information_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    minutes_model_run_id: Mapped[int] = mapped_column(ForeignKey("nba_minutes_model_runs.id"), nullable=False)
    participation_model_run_id: Mapped[int] = mapped_column(ForeignKey("nba_minutes_model_runs.id"), nullable=False, index=True)
    rotation_model_run_id: Mapped[int] = mapped_column(ForeignKey("nba_minutes_model_runs.id"), nullable=False)
    reconciliation_model_run_id: Mapped[int | None] = mapped_column(ForeignKey("nba_minutes_model_runs.id"), nullable=True)

    p_play: Mapped[float] = mapped_column(Float, nullable=False)
    p_rotation: Mapped[float] = mapped_column(Float, nullable=False)
    expected_minutes_if_plays: Mapped[float | None] = mapped_column(Float, nullable=True)  # NULL: no history, not estimated
    reconciled_minutes_if_plays: Mapped[float | None] = mapped_column(Float, nullable=True)  # NULL unless adopted
    quantiles: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tier: Mapped[str] = mapped_column(String(24), nullable=False)
    team_source: Mapped[str] = mapped_column(String(32), nullable=False)
    roster_listed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    history_games: Mapped[int] = mapped_column(Integer, nullable=False)
    availability_evidence: Mapped[dict] = mapped_column(JSON, nullable=False)
    experimental_rules: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    inputs: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    def __repr__(self) -> str:
        return f"<NbaRotationPrediction player={self.player_id} game={self.game_id} p_play={self.p_play:.2f}>"
