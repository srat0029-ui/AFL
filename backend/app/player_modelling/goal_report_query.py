"""Reads persisted goal model runs/metrics/predictions for the API layer -
same "don't recompute per request" rationale as
disposal_report_query.py, mirrored here for the goal-specific tables.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import GoalModelRun, GoalModelValidationMetric, Player, PlayerGoalPrediction
from app.player_modelling.goal_confidence import GoalConfidenceInputs, classify_goal_confidence
from app.player_modelling.goal_distribution import HurdleDistribution, NegativeBinomialGoalDistribution


class GoalModelsUnavailableError(Exception):
    pass


def _metrics_dict(db: Session, run_id: int) -> dict[tuple[str, str], GoalModelValidationMetric]:
    rows = db.scalars(select(GoalModelValidationMetric).where(GoalModelValidationMetric.model_run_id == run_id)).all()
    return {(m.segment, m.metric_name): m for m in rows}


def load_promoted_goal_run(db: Session) -> GoalModelRun:
    run = db.scalar(select(GoalModelRun).where(GoalModelRun.is_promoted.is_(True)))
    if run is None:
        raise GoalModelsUnavailableError("No promoted goal model run found - run `python -m app.player_modelling.goal_cli` first.")
    return run


def load_all_goal_runs(db: Session) -> list[GoalModelRun]:
    return list(db.scalars(select(GoalModelRun).order_by(GoalModelRun.model_name)).all())


def goal_run_summary(db: Session, run: GoalModelRun) -> dict:
    metrics = _metrics_dict(db, run.id)
    mae = metrics.get(("overall", "mae"))
    rmse = metrics.get(("overall", "rmse"))
    bias = metrics.get(("overall", "bias"))
    return {
        "model_name": run.model_name,
        "market": run.market,
        "is_promoted": run.is_promoted,
        "distribution_kind": run.distribution_kind,
        "feature_names": run.feature_names,
        "tune_start_year": run.tune_start_year,
        "tune_end_year": run.tune_end_year,
        "evaluation_start_year": run.evaluation_start_year,
        "evaluation_end_year": run.evaluation_end_year,
        "run_at": run.run_at,
        "overall_mae": mae.value if mae else None,
        "overall_rmse": rmse.value if rmse else None,
        "overall_bias": bias.value if bias else None,
        "evaluation_n": mae.n if mae else None,
    }


@dataclass(frozen=True)
class GoalBacktestSummary:
    promoted: dict
    baselines: list[dict]
    candidates: list[dict]
    season_breakdown: list[dict]
    zero_goal: dict


def load_goal_backtest_summary(db: Session) -> GoalBacktestSummary:
    promoted_run = load_promoted_goal_run(db)
    all_runs = load_all_goal_runs(db)
    promoted_metrics = _metrics_dict(db, promoted_run.id)

    def _overall(run: GoalModelRun) -> dict:
        m = _metrics_dict(db, run.id)
        mae = m.get(("overall", "mae"))
        rmse = m.get(("overall", "rmse"))
        bias = m.get(("overall", "bias"))
        return {"model_name": run.model_name, "mae": mae.value if mae else None, "rmse": rmse.value if rmse else None, "bias": bias.value if bias else None}

    baselines = [_overall(r) for r in all_runs if r.model_name.startswith("goals_baseline_")]
    candidates = [_overall(r) for r in all_runs if not r.model_name.startswith("goals_baseline_")]

    season_breakdown = []
    for (segment, metric_name), m in promoted_metrics.items():
        if segment.startswith("season_") and metric_name == "mae":
            year = int(segment.removeprefix("season_"))
            bias_m = promoted_metrics.get((segment, "bias"))
            season_breakdown.append({"season_year": year, "n": m.n, "mae": m.value, "bias": bias_m.value if bias_m else None})
    season_breakdown.sort(key=lambda r: r["season_year"])

    zero_goal = {
        "brier": promoted_metrics[("zero_goal", "brier")].value,
        "log_loss": promoted_metrics[("zero_goal", "log_loss")].value,
        "ece": promoted_metrics[("zero_goal", "ece")].value if ("zero_goal", "ece") in promoted_metrics else None,
        "mean_predicted_p0": promoted_metrics[("zero_goal", "mean_predicted_p0")].value,
        "actual_p0": promoted_metrics[("zero_goal", "actual_p0")].value,
    }

    return GoalBacktestSummary(
        promoted=goal_run_summary(db, promoted_run), baselines=baselines, candidates=candidates,
        season_breakdown=season_breakdown, zero_goal=zero_goal,
    )


def load_goal_calibration_report(db: Session, model_name: str | None = None) -> dict:
    run = load_promoted_goal_run(db) if model_name is None else db.scalar(select(GoalModelRun).where(GoalModelRun.model_name == model_name))
    if run is None:
        raise GoalModelsUnavailableError(f"No goal model run named {model_name!r}")
    metrics = _metrics_dict(db, run.id)

    thresholds = []
    for t in (1, 2, 3, 4, 5):
        segment = f"threshold_{t}"
        brier = metrics.get((segment, "brier"))
        log_loss = metrics.get((segment, "log_loss"))
        ece = metrics.get((segment, "ece"))
        n_positive = metrics.get((segment, "n_positive"))
        if brier is None:
            continue
        thresholds.append(
            {
                "threshold": float(t),
                "n": brier.n,
                "n_positive": int(n_positive.value) if n_positive else 0,
                "brier": brier.value,
                "log_loss": log_loss.value if log_loss else float("nan"),
                "ece": ece.value if ece else None,
            }
        )

    return {"model_name": run.model_name, "distribution_kind": run.distribution_kind, "thresholds": thresholds}


def load_goal_player_predictions(db: Session, player_id: int, model_name: str | None = None) -> dict:
    player = db.get(Player, player_id)
    if player is None:
        raise GoalModelsUnavailableError(f"No player with id {player_id}")

    run = load_promoted_goal_run(db) if model_name is None else db.scalar(select(GoalModelRun).where(GoalModelRun.model_name == model_name))
    if run is None:
        raise GoalModelsUnavailableError(f"No goal model run named {model_name!r}")

    rows = db.scalars(
        select(PlayerGoalPrediction)
        .where(PlayerGoalPrediction.model_run_id == run.id, PlayerGoalPrediction.player_id == player_id)
        .order_by(PlayerGoalPrediction.season_year)
    ).all()

    league_low_tog_cutoff = 60.0

    predictions = []
    for r in rows:
        if r.distribution_kind == "hurdle":
            dist = HurdleDistribution(p_score=r.p_score, mu_scored=r.mu_scored, alpha_scored=r.alpha_scored)
        else:
            dist = NegativeBinomialGoalDistribution(mu=r.predicted_mean, alpha=r.nb_alpha)

        tier = classify_goal_confidence(
            GoalConfidenceInputs(
                games_of_history=r.games_of_history, tog_last5_avg=None, goals_last5_std=None, league_low_tog_cutoff=league_low_tog_cutoff
            )
        )
        predictions.append(
            {
                "match_id": r.match_id,
                "season_year": r.season_year,
                "games_of_history": r.games_of_history,
                "predicted_mean": r.predicted_mean,
                "actual_goals": r.actual_goals,
                "confidence_tier": tier.value,
                "prob_1_plus": dist.prob_at_least(1),
                "prob_2_plus": dist.prob_at_least(2),
                "prob_3_plus": dist.prob_at_least(3),
                "prob_4_plus": dist.prob_at_least(4),
                "prob_5_plus": dist.prob_at_least(5),
            }
        )

    return {"player": player, "model_name": run.model_name, "predictions": predictions}
