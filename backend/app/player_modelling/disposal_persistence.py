"""Persists disposal model runs, their validation metrics, and their
individual eval-period predictions - Section 22 of the brief. Mirrors
app/modelling/model_run_persistence.py's upsert-by-name convention (one
PlayerModelRun row per model_name, replaced wholesale on each real
backtest run) but writes to the player-specific tables
(app/models/player_model_run.py) so team and player model history stay
fully separate, as the brief requires.
"""

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import PlayerDisposalPrediction, PlayerModelRun, PlayerModelValidationMetric
from app.player_modelling.disposal_backtest import EVALUATION_START_YEAR, PredictionRecord
from app.player_modelling.disposal_evaluation import CALIBRATION_THRESHOLDS, INTERVAL_COVERAGES, ModelEvaluation
from app.player_modelling.market import PlayerMarket


def persist_model_run(
    db: Session,
    model_name: str,
    feature_names: tuple[str, ...],
    config: dict,
    distribution_method: str,
    tune_start_year: int,
    tune_end_year: int,
    evaluation: ModelEvaluation,
    predictions: list[PredictionRecord],
    is_promoted: bool = False,
) -> PlayerModelRun:
    """Upserts one PlayerModelRun (by model_name), replacing its metrics and
    predictions wholesale - the same recompute-and-replace approach
    ModelRun already uses, appropriate here too since a disposal backtest
    is fully deterministic given the same DB state (see
    tests/test_disposal_backtest.py's determinism test)."""
    run = db.scalar(select(PlayerModelRun).where(PlayerModelRun.model_name == model_name))
    now = datetime.now(timezone.utc)
    eval_years = sorted({p.season_year for p in predictions}) or [EVALUATION_START_YEAR]

    if run is None:
        run = PlayerModelRun(
            model_name=model_name,
            market=PlayerMarket.DISPOSALS.value,
            feature_names=list(feature_names),
            config_json=config,
            distribution_method=distribution_method,
            tune_start_year=tune_start_year,
            tune_end_year=tune_end_year,
            evaluation_start_year=eval_years[0],
            evaluation_end_year=eval_years[-1],
            is_promoted=is_promoted,
            run_at=now,
        )
        db.add(run)
        db.flush()
    else:
        run.market = PlayerMarket.DISPOSALS.value
        run.feature_names = list(feature_names)
        run.config_json = config
        run.distribution_method = distribution_method
        run.tune_start_year = tune_start_year
        run.tune_end_year = tune_end_year
        run.evaluation_start_year = eval_years[0]
        run.evaluation_end_year = eval_years[-1]
        run.is_promoted = is_promoted
        run.run_at = now
        db.execute(delete(PlayerModelValidationMetric).where(PlayerModelValidationMetric.model_run_id == run.id))
        db.execute(delete(PlayerDisposalPrediction).where(PlayerDisposalPrediction.model_run_id == run.id))
        db.flush()

    _persist_metrics(db, run.id, evaluation)
    _persist_predictions(db, run.id, predictions)

    db.commit()
    db.refresh(run)
    return run


def _persist_metrics(db: Session, run_id: int, evaluation: ModelEvaluation) -> None:
    pm = evaluation.point
    rows = [
        PlayerModelValidationMetric(model_run_id=run_id, segment="overall", metric_name="mae", n=pm.n, value=pm.mae),
        PlayerModelValidationMetric(model_run_id=run_id, segment="overall", metric_name="rmse", n=pm.n, value=pm.rmse),
        PlayerModelValidationMetric(model_run_id=run_id, segment="overall", metric_name="bias", n=pm.n, value=pm.bias),
        PlayerModelValidationMetric(model_run_id=run_id, segment="overall", metric_name="median_ae", n=pm.n, value=pm.median_ae),
        PlayerModelValidationMetric(model_run_id=run_id, segment="overall", metric_name="within_2", n=pm.n, value=pm.within_2),
        PlayerModelValidationMetric(model_run_id=run_id, segment="overall", metric_name="within_5", n=pm.n, value=pm.within_5),
        PlayerModelValidationMetric(model_run_id=run_id, segment="overall", metric_name="within_10", n=pm.n, value=pm.within_10),
    ]
    for year, season_pm in evaluation.point_by_season.items():
        segment = f"season_{year}"
        rows.append(PlayerModelValidationMetric(model_run_id=run_id, segment=segment, metric_name="mae", n=season_pm.n, value=season_pm.mae))
        rows.append(PlayerModelValidationMetric(model_run_id=run_id, segment=segment, metric_name="rmse", n=season_pm.n, value=season_pm.rmse))
        rows.append(PlayerModelValidationMetric(model_run_id=run_id, segment=segment, metric_name="bias", n=season_pm.n, value=season_pm.bias))

    for t in CALIBRATION_THRESHOLDS:
        tm = evaluation.thresholds[t]
        segment = f"threshold_{t}"
        rows.append(PlayerModelValidationMetric(model_run_id=run_id, segment=segment, metric_name="brier", n=tm.n, value=tm.brier))
        rows.append(PlayerModelValidationMetric(model_run_id=run_id, segment=segment, metric_name="log_loss", n=tm.n, value=tm.log_loss))
        if tm.ece is not None:
            rows.append(PlayerModelValidationMetric(model_run_id=run_id, segment=segment, metric_name="ece", n=tm.n, value=tm.ece))

    for c in INTERVAL_COVERAGES:
        im = evaluation.intervals[c]
        segment = f"interval_{int(c*100)}"
        rows.append(
            PlayerModelValidationMetric(model_run_id=run_id, segment=segment, metric_name="coverage", n=im.n, value=im.empirical_coverage)
        )
        rows.append(PlayerModelValidationMetric(model_run_id=run_id, segment=segment, metric_name="width", n=im.n, value=im.mean_width))

    db.add_all(rows)


def _persist_predictions(db: Session, run_id: int, predictions: list[PredictionRecord]) -> None:
    db.add_all(
        [
            PlayerDisposalPrediction(
                model_run_id=run_id,
                player_id=p.player_id,
                match_id=p.match_id,
                team_id=p.team_id,
                season_year=p.season_year,
                games_of_history=p.games_of_history,
                predicted_mean=p.predicted_mean,
                nb_alpha=p.nb_alpha,
                actual_disposals=p.actual,
            )
            for p in predictions
        ]
    )
