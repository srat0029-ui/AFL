"""Goal-model equivalent of app/models/player_model_run.py — kept as its
own separate set of tables (not shared with the disposal tables), per the
goal-prediction stage brief's explicit "store goal-model runs separately"
instruction and "do not overwrite historical research runs." Mirrors that
module's upsert-by-model_name convention exactly; see its docstring for the
full rationale, unchanged here.

The one real schema difference: a goal prediction may come from either a
single-process NB distribution (mu via predicted_mean, nb_alpha) or a
two-part hurdle distribution (p_score, mu_scored, alpha_scored) - see
app/player_modelling/goal_distribution.py. PlayerGoalPrediction carries
both sets of columns, nullable, with distribution_kind saying which one
to reconstruct.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class GoalModelRun(TimestampMixin, Base):
    __tablename__ = "goal_model_runs"
    __table_args__ = (UniqueConstraint("model_name", name="uq_goal_model_run_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    model_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    feature_names: Mapped[list] = mapped_column(JSON, nullable=False)
    config_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    distribution_kind: Mapped[str] = mapped_column(String(32), nullable=False)  # "nb" | "hurdle"
    tune_start_year: Mapped[int] = mapped_column(Integer, nullable=False)
    tune_end_year: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluation_start_year: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluation_end_year: Mapped[int] = mapped_column(Integer, nullable=False)
    is_promoted: Mapped[bool] = mapped_column(nullable=False, default=False)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    metrics: Mapped[list["GoalModelValidationMetric"]] = relationship(back_populates="model_run", cascade="all, delete-orphan")
    predictions: Mapped[list["PlayerGoalPrediction"]] = relationship(back_populates="model_run", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<GoalModelRun {self.model_name} run_at={self.run_at}>"


class GoalModelValidationMetric(TimestampMixin, Base):
    __tablename__ = "goal_model_validation_metrics"
    __table_args__ = (UniqueConstraint("model_run_id", "segment", "metric_name", name="uq_goal_validation_run_segment_metric"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    model_run_id: Mapped[int] = mapped_column(ForeignKey("goal_model_runs.id"), nullable=False, index=True)
    segment: Mapped[str] = mapped_column(String(64), nullable=False)  # "overall" | "season_2021" | "threshold_1" | ...
    metric_name: Mapped[str] = mapped_column(String(32), nullable=False)
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    naive_baseline_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    model_run: Mapped["GoalModelRun"] = relationship(back_populates="metrics")

    def __repr__(self) -> str:
        return f"<GoalModelValidationMetric {self.segment}:{self.metric_name}={self.value:.4f}>"


class PlayerGoalPrediction(TimestampMixin, Base):
    __tablename__ = "player_goal_predictions"
    __table_args__ = (UniqueConstraint("model_run_id", "player_id", "match_id", name="uq_player_goal_prediction"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    model_run_id: Mapped[int] = mapped_column(ForeignKey("goal_model_runs.id"), nullable=False, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False, index=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    season_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    games_of_history: Mapped[int] = mapped_column(Integer, nullable=False)
    predicted_mean: Mapped[float] = mapped_column(Float, nullable=False)
    distribution_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    nb_alpha: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    mu_scored: Mapped[float | None] = mapped_column(Float, nullable=True)
    alpha_scored: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_goals: Mapped[int] = mapped_column(Integer, nullable=False)

    model_run: Mapped["GoalModelRun"] = relationship(back_populates="predictions")

    def __repr__(self) -> str:
        return f"<PlayerGoalPrediction player={self.player_id} match={self.match_id} predicted={self.predicted_mean:.2f} actual={self.actual_goals}>"
