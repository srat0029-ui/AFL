"""Records what config a model is currently using and how well it actually
validated, per market — the thing Stage 1.5's confidence tiering reads
before trusting any edge number.

One ModelRun row per model_name ('elo' | 'poisson'), upserted wholesale each
time the corresponding CLI (elo_cli.py / poisson_cli.py) runs — same
recompute-and-replace philosophy as EloRating/PoissonMatchPrediction. Its
ModelValidationMetric children capture the honest tune/holdout numbers
already computed by that CLI run (e.g. Poisson's total-points market
genuinely has no edge over a naive baseline — see poisson_cli.py) so that
fact lives in one place instead of being re-derived or, worse, silently
assumed away when computing a live edge.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class ModelRun(TimestampMixin, Base):
    __tablename__ = "model_runs"
    __table_args__ = (UniqueConstraint("model_name", name="uq_model_run_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    model_name: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # "elo" | "poisson"
    config_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    tune_end_year: Mapped[int] = mapped_column(Integer, nullable=False)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    metrics: Mapped[list["ModelValidationMetric"]] = relationship(
        back_populates="model_run", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<ModelRun {self.model_name} run_at={self.run_at}>"


class ModelValidationMetric(TimestampMixin, Base):
    __tablename__ = "model_validation_metrics"
    __table_args__ = (UniqueConstraint("model_run_id", "market_type", name="uq_validation_run_market"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    model_run_id: Mapped[int] = mapped_column(ForeignKey("model_runs.id"), nullable=False, index=True)
    market_type: Mapped[str] = mapped_column(String(32), nullable=False)  # "h2h" | "line" | "total"
    metric_name: Mapped[str] = mapped_column(String(32), nullable=False)  # "brier_score" | "mae"
    holdout_n: Mapped[int] = mapped_column(Integer, nullable=False)
    holdout_value: Mapped[float] = mapped_column(Float, nullable=False)
    naive_baseline_value: Mapped[float] = mapped_column(Float, nullable=False)
    has_edge_over_naive: Mapped[bool] = mapped_column(Boolean, nullable=False)

    model_run: Mapped["ModelRun"] = relationship(back_populates="metrics")

    def __repr__(self) -> str:
        return f"<ModelValidationMetric {self.market_type}:{self.metric_name}={self.holdout_value:.4f}>"


class ModelRunHistory(TimestampMixin, Base):
    """An archived snapshot of a ModelRun (config + its validation metrics)
    from just before it was overwritten by a newer run — see
    app/modelling/model_run_persistence.py. Exists so promoting a revised
    config (e.g. the Poisson season-transition fix) doesn't destroy the
    ability to reproduce or compare against what was live before; the live
    ModelRun table itself stays a single upserted-in-place row per
    model_name, unchanged, so nothing that reads it needs to change."""

    __tablename__ = "model_run_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_name: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    config_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    tune_end_year: Mapped[int] = mapped_column(Integer, nullable=False)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # when this config was originally made live
    metrics_json: Mapped[list] = mapped_column(JSON, nullable=False)  # snapshot of its ModelValidationMetric rows
    superseded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # when it was replaced

    def __repr__(self) -> str:
        return f"<ModelRunHistory {self.model_name} run_at={self.run_at} superseded_at={self.superseded_at}>"
