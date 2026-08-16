"""Turns disposal_backtest.py's PredictionRecord lists into the metrics the
disposal-prediction stage brief asks for: point-prediction accuracy
(Section 12), threshold-probability calibration (Section 13), and
prediction-interval coverage (Section 14). Reuses app/modelling/metrics.py
wherever the metric is genuinely the same computation as the team-level
models already use (mae/rmse/bias/brier_score/log_loss/calibration_table/
expected_calibration_error); disposal_metrics.py adds the count-specific
ones (median AE, within-K accuracy, interval coverage/width).
"""

from dataclasses import dataclass

from app.modelling.metrics import bias, brier_score, calibration_table, expected_calibration_error, log_loss, mae, rmse
from app.player_modelling.disposal_backtest import PredictionRecord
from app.player_modelling.disposal_metrics import interval_coverage, mean_interval_width, median_absolute_error, within_k_accuracy

THRESHOLDS = (15, 20, 25, 30, 35, 40)
HALF_LINES = (19.5, 24.5, 29.5, 34.5)
CALIBRATION_THRESHOLDS = (20, 25, 30, 35)
INTERVAL_COVERAGES = (0.5, 0.8, 0.9)


@dataclass(frozen=True)
class PointMetrics:
    n: int
    mae: float
    rmse: float
    bias: float
    median_ae: float
    within_2: float
    within_5: float
    within_10: float


def compute_point_metrics(predictions: list[PredictionRecord]) -> PointMetrics:
    preds = [p.predicted_mean for p in predictions]
    actuals = [float(p.actual) for p in predictions]
    return PointMetrics(
        n=len(predictions),
        mae=mae(preds, actuals),
        rmse=rmse(preds, actuals),
        bias=bias(preds, actuals),
        median_ae=median_absolute_error(preds, actuals),
        within_2=within_k_accuracy(preds, actuals, 2),
        within_5=within_k_accuracy(preds, actuals, 5),
        within_10=within_k_accuracy(preds, actuals, 10),
    )


def point_metrics_by_season(predictions: list[PredictionRecord]) -> dict[int, PointMetrics]:
    seasons = sorted({p.season_year for p in predictions})
    return {year: compute_point_metrics([p for p in predictions if p.season_year == year]) for year in seasons}


@dataclass(frozen=True)
class ThresholdMetrics:
    threshold: float
    n: int
    brier: float
    log_loss: float
    ece: float | None
    calibration: list[dict]


def compute_threshold_metrics(
    predictions: list[PredictionRecord], threshold: float, distribution: str = "nb"
) -> ThresholdMetrics:
    """distribution: "nb" (NegativeBinomialDistribution) or "empirical"
    (EmpiricalResidualDistribution) - see disposal_distribution.py. Kept
    selectable rather than hardcoded so disposal_backtest_cli.py can
    compare both and report which one actually calibrates better, per the
    brief's explicit instruction not to assume a distributional family
    without checking."""
    probs = []
    outcomes = []
    for p in predictions:
        dist = p.nb_distribution() if distribution == "nb" else p.empirical_distribution()
        probs.append(dist.prob_at_least(threshold))
        outcomes.append(1.0 if p.actual >= threshold else 0.0)

    cal = calibration_table(probs, outcomes, n_bins=10)
    return ThresholdMetrics(
        threshold=threshold,
        n=len(predictions),
        brier=brier_score(probs, outcomes),
        log_loss=log_loss(probs, outcomes),
        ece=expected_calibration_error(cal),
        calibration=cal,
    )


@dataclass(frozen=True)
class IntervalMetrics:
    coverage_target: float
    n: int
    empirical_coverage: float
    mean_width: float


def compute_interval_metrics(predictions: list[PredictionRecord], coverage: float, distribution: str = "nb") -> IntervalMetrics:
    lowers, uppers = [], []
    for p in predictions:
        dist = p.nb_distribution() if distribution == "nb" else p.empirical_distribution()
        lo, hi = dist.interval(coverage)
        lowers.append(lo)
        uppers.append(hi)
    actuals = [float(p.actual) for p in predictions]
    return IntervalMetrics(
        coverage_target=coverage,
        n=len(predictions),
        empirical_coverage=interval_coverage(lowers, uppers, actuals),
        mean_width=mean_interval_width(lowers, uppers),
    )


@dataclass(frozen=True)
class ModelEvaluation:
    model_name: str
    point: PointMetrics
    point_by_season: dict[int, PointMetrics]
    thresholds: dict[float, ThresholdMetrics]
    intervals: dict[float, IntervalMetrics]
    distribution_method: str


def evaluate_model(model_name: str, predictions: list[PredictionRecord], distribution: str = "nb") -> ModelEvaluation:
    return ModelEvaluation(
        model_name=model_name,
        point=compute_point_metrics(predictions),
        point_by_season=point_metrics_by_season(predictions),
        thresholds={t: compute_threshold_metrics(predictions, t, distribution) for t in CALIBRATION_THRESHOLDS},
        intervals={c: compute_interval_metrics(predictions, c, distribution) for c in INTERVAL_COVERAGES},
        distribution_method=distribution,
    )


def best_distribution_method(predictions: list[PredictionRecord]) -> str:
    """Compares NB vs empirical-residual distributions on THIS model's own
    eval predictions using average ECE across the four calibration
    thresholds - the concrete, data-driven answer to Section 8's
    instruction not to assume a distributional family. Ties go to "nb"
    (the more compact, parametric option) since a negligible calibration
    difference isn't worth the extra storage/complexity of persisting a
    residual sample per prediction."""
    nb_ece = [compute_threshold_metrics(predictions, t, "nb").ece for t in CALIBRATION_THRESHOLDS]
    emp_ece = [compute_threshold_metrics(predictions, t, "empirical").ece for t in CALIBRATION_THRESHOLDS]
    nb_avg = sum(e for e in nb_ece if e is not None) / max(1, len([e for e in nb_ece if e is not None]))
    emp_avg = sum(e for e in emp_ece if e is not None) / max(1, len([e for e in emp_ece if e is not None]))
    return "empirical" if emp_avg < nb_avg * 0.95 else "nb"
