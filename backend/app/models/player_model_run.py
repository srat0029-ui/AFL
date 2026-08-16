"""Player-level equivalent of app/models/model_run.py — kept as its own set
of tables (not reusing ModelRun/ModelValidationMetric) because a player
model run carries genuinely different shape: a feature list, a
tune/evaluation year split, a distribution method, AND (unlike the
team-level models) enough individual per-row predictions to reproduce the
example-prediction/calibration/by-player views without re-running the full
backtest — see PlayerDisposalPrediction below. One row per model_name
(e.g. "disposals_ridge"), upserted wholesale on each real backtest run,
same recompute-and-replace convention as ModelRun.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class PlayerModelRun(TimestampMixin, Base):
    __tablename__ = "player_model_runs"
    __table_args__ = (UniqueConstraint("model_name", name="uq_player_model_run_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    model_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # e.g. "disposals_ridge"
    market: Mapped[str] = mapped_column(String(32), nullable=False)  # PlayerMarket value, e.g. "player_disposals"
    feature_names: Mapped[list] = mapped_column(JSON, nullable=False)
    config_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    distribution_method: Mapped[str] = mapped_column(String(32), nullable=False)  # "nb" | "empirical"
    tune_start_year: Mapped[int] = mapped_column(Integer, nullable=False)
    tune_end_year: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluation_start_year: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluation_end_year: Mapped[int] = mapped_column(Integer, nullable=False)
    is_promoted: Mapped[bool] = mapped_column(nullable=False, default=False)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    metrics: Mapped[list["PlayerModelValidationMetric"]] = relationship(
        back_populates="model_run", cascade="all, delete-orphan"
    )
    predictions: Mapped[list["PlayerDisposalPrediction"]] = relationship(
        back_populates="model_run", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<PlayerModelRun {self.model_name} run_at={self.run_at}>"


class PlayerModelValidationMetric(TimestampMixin, Base):
    """Aggregate metrics for one model_run - e.g. metric_name="mae",
    segment="overall" or segment="season_2021" or segment="threshold_20"."""

    __tablename__ = "player_model_validation_metrics"
    __table_args__ = (UniqueConstraint("model_run_id", "segment", "metric_name", name="uq_player_validation_run_segment_metric"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    model_run_id: Mapped[int] = mapped_column(ForeignKey("player_model_runs.id"), nullable=False, index=True)
    segment: Mapped[str] = mapped_column(String(64), nullable=False)  # "overall" | "season_2021" | "threshold_20" | "history_<10" | ...
    metric_name: Mapped[str] = mapped_column(String(32), nullable=False)  # "mae" | "rmse" | "bias" | "brier" | "ece" | "coverage_80" | ...
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    naive_baseline_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    model_run: Mapped["PlayerModelRun"] = relationship(back_populates="metrics")

    def __repr__(self) -> str:
        return f"<PlayerModelValidationMetric {self.segment}:{self.metric_name}={self.value:.4f}>"


class PlayerDisposalPrediction(TimestampMixin, Base):
    """One eval-period prediction from one model_run - persisted (not just
    aggregate metrics) so example predictions, by-player history, and
    calibration tables are reproducible from the DB without re-running the
    backtest. NB parametrisation (predicted_mean, nb_alpha) is stored
    rather than a raw residual sample - compact, and sufficient to
    reconstruct any threshold probability or interval on demand via
    app/player_modelling/disposal_distribution.NegativeBinomialDistribution
    (see disposal_persistence.py)."""

    __tablename__ = "player_disposal_predictions"
    __table_args__ = (UniqueConstraint("model_run_id", "player_id", "match_id", name="uq_player_disposal_prediction"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    model_run_id: Mapped[int] = mapped_column(ForeignKey("player_model_runs.id"), nullable=False, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False, index=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    season_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    games_of_history: Mapped[int] = mapped_column(Integer, nullable=False)
    predicted_mean: Mapped[float] = mapped_column(Float, nullable=False)
    nb_alpha: Mapped[float] = mapped_column(Float, nullable=False)
    actual_disposals: Mapped[int] = mapped_column(Integer, nullable=False)

    model_run: Mapped["PlayerModelRun"] = relationship(back_populates="predictions")

    def __repr__(self) -> str:
        return f"<PlayerDisposalPrediction player={self.player_id} match={self.match_id} predicted={self.predicted_mean:.1f} actual={self.actual_disposals}>"
