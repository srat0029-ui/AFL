"""Expected-minutes model runs and frozen expected-minutes predictions.

Both tables are append-only (protected in app/models/nba/__init__.py):

- `NbaMinutesModelRun` is one fitted-and-evaluated model configuration: what
  it was trained on, what it was evaluated on, its features and
  hyperparameters, its metrics, the code version, and - for a run that can
  serve predictions - the path and SHA-256 of the fitted model file. A new
  experiment is a NEW row; an older result is never overwritten.

- `NbaMinutesPrediction` is one expected-minutes prediction for one player in
  one upcoming game, frozen before tip-off with the information cutoff it
  was made at. Nothing here records the outcome: a game's actual minutes
  live in nba_player_game_logs and are joined only after the game is final.

Minutes here are "minutes given the player plays": a player prop is void if
the player does not play, so that is the quantity the downstream stat models
need. Whether he plays at all is a separate question (Model V2 territory,
using prospective availability evidence).
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class NbaMinutesModelRun(TimestampMixin, Base):
    __tablename__ = "nba_minutes_model_runs"
    __table_args__ = (UniqueConstraint("run_key", name="uq_nba_minutes_model_run_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # Unique per run, e.g. "minutes-v1-hgb:2026-10-03T12:00:00Z".
    run_key: Mapped[str] = mapped_column(String(160), nullable=False)
    model_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # "evaluation" (a walk-forward/holdout experiment) or "serving" (a model
    # fitted on all eligible history, whose file serves prospective predictions).
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    code_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Latest tip-off of any box score in the dataset the run saw.
    dataset_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    training_seasons: Mapped[list] = mapped_column(JSON, nullable=False)
    evaluation_seasons: Mapped[list] = mapped_column(JSON, nullable=False)
    features: Mapped[list] = mapped_column(JSON, nullable=False)
    hyperparameters: Mapped[dict] = mapped_column(JSON, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False)
    # Residual quantiles by predicted-minutes band, used as the V1 uncertainty.
    uncertainty: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    artifact_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<NbaMinutesModelRun {self.run_key}>"


class NbaMinutesPrediction(TimestampMixin, Base):
    __tablename__ = "nba_minutes_predictions"
    __table_args__ = (
        UniqueConstraint("game_id", "player_id", "model_run_id", "information_cutoff", name="uq_nba_minutes_prediction_identity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    model_run_id: Mapped[int] = mapped_column(ForeignKey("nba_minutes_model_runs.id"), nullable=False, index=True)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("nba_games.id"), nullable=False, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("nba_players.id"), nullable=False, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("nba_teams.id"), nullable=False, index=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Nothing learned after this instant fed the prediction. Always <= generated_at < tip-off.
    information_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    expected_minutes: Mapped[float] = mapped_column(Float, nullable=False)
    # {"p10": .., "p25": .., "p50": .., "p75": .., "p90": ..} in minutes.
    quantiles: Mapped[dict] = mapped_column(JSON, nullable=False)
    history_games: Mapped[int] = mapped_column(Integer, nullable=False)
    # How the player was assigned to the team (e.g. "roster_observation", "last_box_score").
    team_source: Mapped[str] = mapped_column(String(32), nullable=False)
    inputs: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    model_run: Mapped["NbaMinutesModelRun"] = relationship(foreign_keys=[model_run_id])

    def __repr__(self) -> str:
        return f"<NbaMinutesPrediction player={self.player_id} game={self.game_id} exp={self.expected_minutes:.1f}>"
