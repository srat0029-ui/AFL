"""Persists goal model runs, their validation metrics, and their
individual eval-period predictions — Section 23. Mirrors
app/player_modelling/disposal_persistence.py's exact upsert-by-name
convention, writing to the SEPARATE goal-specific tables
(app/models/goal_model_run.py) per the brief's "store goal-model runs
separately" instruction.
"""

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import GoalModelRun, GoalModelValidationMetric, PlayerGoalPrediction
from app.player_modelling.goal_backtest import EVALUATION_START_YEAR, GoalPredictionRecord
from app.player_modelling.goal_evaluation import THRESHOLDS, GoalModelEvaluation
from app.player_modelling.market import PlayerMarket


def persist_goal_model_run(
    db: Session,
    model_name: str,
    feature_names: tuple[str, ...],
    config: dict,
    distribution_kind: str,
    tune_start_year: int,
    tune_end_year: int,
    evaluation: GoalModelEvaluation,
    predictions: list[GoalPredictionRecord],
    is_promoted: bool = False,
) -> GoalModelRun:
    run = db.scalar(select(GoalModelRun).where(GoalModelRun.model_name == model_name))
    now = datetime.now(timezone.utc)
    eval_years = sorted({p.season_year for p in predictions}) or [EVALUATION_START_YEAR]

    if run is None:
        run = GoalModelRun(
            model_name=model_name,
            market=PlayerMarket.GOALS.value,
            feature_names=list(feature_names),
            config_json=config,
            distribution_kind=distribution_kind,
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
        run.market = PlayerMarket.GOALS.value
        run.feature_names = list(feature_names)
        run.config_json = config
        run.distribution_kind = distribution_kind
        run.tune_start_year = tune_start_year
        run.tune_end_year = tune_end_year
        run.evaluation_start_year = eval_years[0]
        run.evaluation_end_year = eval_years[-1]
        run.is_promoted = is_promoted
        run.run_at = now
        db.execute(delete(GoalModelValidationMetric).where(GoalModelValidationMetric.model_run_id == run.id))
        db.execute(delete(PlayerGoalPrediction).where(PlayerGoalPrediction.model_run_id == run.id))
        db.flush()

    _persist_metrics(db, run.id, evaluation)
    _persist_predictions(db, run.id, predictions)

    db.commit()
    db.refresh(run)
    return run


def _persist_metrics(db: Session, run_id: int, evaluation: GoalModelEvaluation) -> None:
    pm = evaluation.point
    rows = [
        GoalModelValidationMetric(model_run_id=run_id, segment="overall", metric_name="mae", n=pm.n, value=pm.mae),
        GoalModelValidationMetric(model_run_id=run_id, segment="overall", metric_name="rmse", n=pm.n, value=pm.rmse),
        GoalModelValidationMetric(model_run_id=run_id, segment="overall", metric_name="bias", n=pm.n, value=pm.bias),
        GoalModelValidationMetric(model_run_id=run_id, segment="overall", metric_name="median_ae", n=pm.n, value=pm.median_ae),
        GoalModelValidationMetric(model_run_id=run_id, segment="overall", metric_name="within_1", n=pm.n, value=pm.within_1),
        GoalModelValidationMetric(
            model_run_id=run_id, segment="overall", metric_name="exact_match_rate", n=pm.n, value=pm.exact_match_rate
        ),
    ]
    for year, season_pm in evaluation.point_by_season.items():
        segment = f"season_{year}"
        rows.append(GoalModelValidationMetric(model_run_id=run_id, segment=segment, metric_name="mae", n=season_pm.n, value=season_pm.mae))
        rows.append(GoalModelValidationMetric(model_run_id=run_id, segment=segment, metric_name="bias", n=season_pm.n, value=season_pm.bias))

    for t in THRESHOLDS:
        tm = evaluation.thresholds[t]
        segment = f"threshold_{t}"
        rows.append(GoalModelValidationMetric(model_run_id=run_id, segment=segment, metric_name="brier", n=tm.n, value=tm.brier))
        rows.append(GoalModelValidationMetric(model_run_id=run_id, segment=segment, metric_name="log_loss", n=tm.n, value=tm.log_loss))
        rows.append(GoalModelValidationMetric(model_run_id=run_id, segment=segment, metric_name="n_positive", n=tm.n, value=float(tm.n_positive)))
        if tm.ece is not None:
            rows.append(GoalModelValidationMetric(model_run_id=run_id, segment=segment, metric_name="ece", n=tm.n, value=tm.ece))

    zg = evaluation.zero_goal
    rows.append(GoalModelValidationMetric(model_run_id=run_id, segment="zero_goal", metric_name="brier", n=zg.n, value=zg.brier))
    rows.append(GoalModelValidationMetric(model_run_id=run_id, segment="zero_goal", metric_name="log_loss", n=zg.n, value=zg.log_loss))
    if zg.ece is not None:
        rows.append(GoalModelValidationMetric(model_run_id=run_id, segment="zero_goal", metric_name="ece", n=zg.n, value=zg.ece))
    rows.append(GoalModelValidationMetric(model_run_id=run_id, segment="zero_goal", metric_name="mean_predicted_p0", n=zg.n, value=zg.mean_predicted_p0))
    rows.append(GoalModelValidationMetric(model_run_id=run_id, segment="zero_goal", metric_name="actual_p0", n=zg.n, value=zg.actual_p0))

    db.add_all(rows)


def _persist_predictions(db: Session, run_id: int, predictions: list[GoalPredictionRecord]) -> None:
    db.add_all(
        [
            PlayerGoalPrediction(
                model_run_id=run_id,
                player_id=p.player_id,
                match_id=p.match_id,
                team_id=p.team_id,
                season_year=p.season_year,
                games_of_history=p.games_of_history,
                predicted_mean=p.predicted_mean,
                distribution_kind=p.distribution_kind,
                nb_alpha=p.nb_alpha,
                p_score=p.p_score,
                mu_scored=p.mu_scored,
                alpha_scored=p.alpha_scored,
                actual_goals=p.actual,
            )
            for p in predictions
        ]
    )
