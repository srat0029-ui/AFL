"""Disposal-specific evaluation metrics not already covered by
app/modelling/metrics.py (mae/rmse/bias/brier_score/log_loss/
calibration_table/expected_calibration_error are reused directly from
there — see disposal_backtest.py's imports). This module adds the metrics
unique to a count-valued point+distribution forecast: median absolute
error, "within K disposals" accuracy, and prediction-interval coverage.
"""

import statistics


def median_absolute_error(predictions: list[float], actuals: list[float]) -> float:
    if not predictions:
        return float("nan")
    return statistics.median(abs(p - a) for p, a in zip(predictions, actuals))


def within_k_accuracy(predictions: list[float], actuals: list[float], k: float) -> float:
    """Fraction of predictions within +/-k disposals of the actual value —
    more interpretable to a non-statistician than MAE/RMSE alone ("87% of
    predictions were within 5 disposals of what actually happened")."""
    if not predictions:
        return float("nan")
    return sum(1 for p, a in zip(predictions, actuals) if abs(p - a) <= k) / len(predictions)


def interval_coverage(lowers: list[float], uppers: list[float], actuals: list[float]) -> float:
    """Fraction of actual outcomes that fell inside their own predicted
    [lower, upper] interval. A well-calibrated 80% interval should score
    close to 0.80 here — see disposal_backtest.py's per-coverage-level
    evaluation (50%/80%/90%)."""
    if not lowers:
        return float("nan")
    return sum(1 for lo, hi, a in zip(lowers, uppers, actuals) if lo <= a <= hi) / len(lowers)


def mean_interval_width(lowers: list[float], uppers: list[float]) -> float:
    if not lowers:
        return float("nan")
    return sum(hi - lo for lo, hi in zip(lowers, uppers)) / len(lowers)
