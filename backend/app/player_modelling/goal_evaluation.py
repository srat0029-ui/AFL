"""Turns goal_backtest.py's GoalPredictionRecord lists into the metrics the
goal-prediction stage brief asks for — point accuracy (Section 11),
threshold-probability calibration (Section 12-13, the brief's most
important section), and zero-goal calibration specifically. Reuses
app/modelling/metrics.py and disposal_metrics.py directly wherever the
computation is identical to the disposals stage's (it is, for all of
these - Brier/log-loss/calibration/ECE/MAE/RMSE/bias don't care what the
underlying count represents).
"""

from dataclasses import dataclass

from app.modelling.metrics import bias, brier_score, calibration_table, expected_calibration_error, log_loss, mae, rmse
from app.player_modelling.disposal_metrics import median_absolute_error, within_k_accuracy
from app.player_modelling.goal_backtest import GoalPredictionRecord

THRESHOLDS = (1, 2, 3, 4, 5)
HALF_LINES = (1.5, 2.5)
# Below this sample size, a calibration bucket's reliability shouldn't be
# read as a strong claim (Section 13: "do not claim excellent calibration
# from tiny bins") - surfaced as a flag on ThresholdMetrics, not hidden.
MIN_RELIABLE_BUCKET_N = 30


@dataclass(frozen=True)
class PointMetrics:
    n: int
    mae: float
    rmse: float
    bias: float
    median_ae: float
    within_1: float
    exact_match_rate: float  # fraction where the ROUNDED prediction exactly equals actual goals - meaningful for a low-count target


def compute_point_metrics(predictions: list[GoalPredictionRecord]) -> PointMetrics:
    preds = [p.predicted_mean for p in predictions]
    actuals = [float(p.actual) for p in predictions]
    rounded = [round(p) for p in preds]
    exact = sum(1 for r, a in zip(rounded, actuals) if r == a) / len(predictions) if predictions else float("nan")
    return PointMetrics(
        n=len(predictions),
        mae=mae(preds, actuals),
        rmse=rmse(preds, actuals),
        bias=bias(preds, actuals),
        median_ae=median_absolute_error(preds, actuals),
        within_1=within_k_accuracy(preds, actuals, 1),
        exact_match_rate=exact,
    )


def point_metrics_by_season(predictions: list[GoalPredictionRecord]) -> dict[int, PointMetrics]:
    seasons = sorted({p.season_year for p in predictions})
    return {year: compute_point_metrics([p for p in predictions if p.season_year == year]) for year in seasons}


@dataclass(frozen=True)
class ThresholdMetrics:
    threshold: float
    n: int
    n_positive: int  # how many rows actually hit this threshold - the real sample backing the "rare event" side
    brier: float
    log_loss: float
    ece: float | None
    calibration: list[dict]
    low_sample_warning: bool  # True if any populated calibration bucket has n < MIN_RELIABLE_BUCKET_N


def compute_threshold_metrics(predictions: list[GoalPredictionRecord], threshold: float) -> ThresholdMetrics:
    probs = []
    outcomes = []
    for p in predictions:
        dist = p.distribution()
        probs.append(dist.prob_at_least(threshold))
        outcomes.append(1.0 if p.actual >= threshold else 0.0)

    cal = calibration_table(probs, outcomes, n_bins=10)
    low_sample = any(0 < row["n"] < MIN_RELIABLE_BUCKET_N for row in cal)
    return ThresholdMetrics(
        threshold=threshold,
        n=len(predictions),
        n_positive=int(sum(outcomes)),
        brier=brier_score(probs, outcomes),
        log_loss=log_loss(probs, outcomes),
        ece=expected_calibration_error(cal),
        calibration=cal,
        low_sample_warning=low_sample,
    )


@dataclass(frozen=True)
class ZeroGoalCalibration:
    """P(goals=0) calibration specifically - the direct measure of whether
    the confirmed zero-inflation (see the real audit) is actually being
    modelled well, not just P(1+) indirectly."""

    n: int
    brier: float
    log_loss: float
    ece: float | None
    mean_predicted_p0: float
    actual_p0: float


def compute_zero_goal_calibration(predictions: list[GoalPredictionRecord]) -> ZeroGoalCalibration:
    probs = [p.distribution().pmf_at(0) for p in predictions]
    outcomes = [1.0 if p.actual == 0 else 0.0 for p in predictions]
    cal = calibration_table(probs, outcomes, n_bins=10)
    return ZeroGoalCalibration(
        n=len(predictions),
        brier=brier_score(probs, outcomes),
        log_loss=log_loss(probs, outcomes),
        ece=expected_calibration_error(cal),
        mean_predicted_p0=sum(probs) / len(probs) if probs else float("nan"),
        actual_p0=sum(outcomes) / len(outcomes) if outcomes else float("nan"),
    )


@dataclass(frozen=True)
class GoalModelEvaluation:
    model_name: str
    point: PointMetrics
    point_by_season: dict[int, PointMetrics]
    thresholds: dict[float, ThresholdMetrics]
    zero_goal: ZeroGoalCalibration


def evaluate_goal_model(model_name: str, predictions: list[GoalPredictionRecord]) -> GoalModelEvaluation:
    return GoalModelEvaluation(
        model_name=model_name,
        point=compute_point_metrics(predictions),
        point_by_season=point_metrics_by_season(predictions),
        thresholds={t: compute_threshold_metrics(predictions, t) for t in THRESHOLDS},
        zero_goal=compute_zero_goal_calibration(predictions),
    )
