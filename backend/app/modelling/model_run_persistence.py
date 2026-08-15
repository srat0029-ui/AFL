"""Persists which config a model is using and its honest per-market
validation quality — see app/models/model_run.py for why this exists.

Shared by elo_cli.py and poisson_cli.py so both models' CLIs record their
results the same way.
"""

from dataclasses import asdict
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ModelRun, ModelRunHistory, ModelValidationMetric


def persist_model_run(
    db: Session,
    model_name: str,
    config,
    tune_end_year: int,
    metrics: list[dict],
) -> ModelRun:
    """metrics: list of {"market_type", "metric_name", "holdout_n",
    "holdout_value", "naive_baseline_value", "has_edge_over_naive"}.

    Before overwriting an existing run, archives its current config +
    metrics to ModelRunHistory — so promoting a revised config doesn't
    destroy the ability to reproduce or compare against what was live
    before. The live ModelRun row itself keeps its original upsert-in-place
    semantics (exactly one row per model_name), so every existing reader
    (edges/calculator.py, evaluation.py, etc.) needs no changes.
    """
    run = db.scalar(select(ModelRun).where(ModelRun.model_name == model_name))
    if run is None:
        run = ModelRun(
            model_name=model_name,
            config_json=asdict(config),
            tune_end_year=tune_end_year,
            run_at=datetime.now(timezone.utc),
        )
        db.add(run)
    else:
        now = datetime.now(timezone.utc)
        db.add(
            ModelRunHistory(
                model_name=run.model_name,
                config_json=run.config_json,
                tune_end_year=run.tune_end_year,
                run_at=run.run_at,
                metrics_json=[
                    {
                        "market_type": m.market_type,
                        "metric_name": m.metric_name,
                        "holdout_n": m.holdout_n,
                        "holdout_value": m.holdout_value,
                        "naive_baseline_value": m.naive_baseline_value,
                        "has_edge_over_naive": m.has_edge_over_naive,
                    }
                    for m in run.metrics
                ],
                superseded_at=now,
            )
        )
        run.config_json = asdict(config)
        run.tune_end_year = tune_end_year
        run.run_at = now
        run.metrics.clear()
    db.flush()

    for m in metrics:
        db.add(
            ModelValidationMetric(
                model_run_id=run.id,
                market_type=m["market_type"],
                metric_name=m["metric_name"],
                holdout_n=m["holdout_n"],
                holdout_value=m["holdout_value"],
                naive_baseline_value=m["naive_baseline_value"],
                has_edge_over_naive=m["has_edge_over_naive"],
            )
        )

    db.commit()
    db.refresh(run)
    return run
