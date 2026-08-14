"""Generic probability-forecast evaluation metrics.

Deliberately model-agnostic (plain lists of floats in/out) so the same
functions serve Elo here, the Poisson model in Stage 1.3, and the full
backtesting report in Stage 1.7 — calibration and scoring rules don't
change based on which model produced the forecast.
"""

import math


def brier_score(predictions: list[float], outcomes: list[float]) -> float:
    """Mean squared error between predicted probability and actual outcome
    (0/1, or 0.5 for a draw). Lower is better; 0 is perfect, 0.25 is what
    always guessing 50% scores against a 50/50 coin.
    """
    if not predictions:
        return float("nan")
    return sum((p - o) ** 2 for p, o in zip(predictions, outcomes)) / len(predictions)


def log_loss(predictions: list[float], outcomes: list[float], eps: float = 1e-15) -> float:
    """Lower is better; heavily penalises confident-and-wrong predictions,
    which Brier score alone doesn't punish as sharply.
    """
    if not predictions:
        return float("nan")
    total = 0.0
    for p, o in zip(predictions, outcomes):
        p = min(max(p, eps), 1 - eps)
        total += -(o * math.log(p) + (1 - o) * math.log(1 - p))
    return total / len(predictions)


def accuracy(predictions: list[float], outcomes: list[float]) -> float:
    """Fraction of matches where the side favoured by the model (prob > 0.5)
    actually won. Draws are excluded from the denominator — there's no
    "favoured side was right" verdict to give when the game itself didn't
    produce a winner.
    """
    correct = 0
    total = 0
    for p, o in zip(predictions, outcomes):
        if o == 0.5:
            continue
        total += 1
        if (p > 0.5) == (o == 1.0):
            correct += 1
    return correct / total if total else float("nan")


def mae(predictions: list[float], actuals: list[float]) -> float:
    """Mean absolute error — for point predictions (expected total points,
    expected margin), not probabilities. Same units as the quantity itself,
    so directly interpretable (e.g. "off by 18 points on average")."""
    if not predictions:
        return float("nan")
    return sum(abs(p - a) for p, a in zip(predictions, actuals)) / len(predictions)


def rmse(predictions: list[float], actuals: list[float]) -> float:
    """Root mean squared error — penalises large misses more than mae does."""
    if not predictions:
        return float("nan")
    return math.sqrt(sum((p - a) ** 2 for p, a in zip(predictions, actuals)) / len(predictions))


def calibration_table(predictions: list[float], outcomes: list[float], n_bins: int = 10) -> list[dict]:
    """Buckets predictions by predicted probability and compares the average
    prediction in each bucket to the actual outcome rate. A well-calibrated
    model has avg_predicted ≈ actual_rate in every bucket with enough
    samples — this is the check that matters more than raw accuracy.
    """
    buckets: list[list[tuple[float, float]]] = [[] for _ in range(n_bins)]
    for p, o in zip(predictions, outcomes):
        idx = min(int(p * n_bins), n_bins - 1)
        buckets[idx].append((p, o))

    rows = []
    for i, bucket in enumerate(buckets):
        lo, hi = i / n_bins, (i + 1) / n_bins
        if not bucket:
            rows.append({"bucket": f"{lo:.1f}-{hi:.1f}", "n": 0, "avg_predicted": None, "actual_rate": None})
            continue
        rows.append(
            {
                "bucket": f"{lo:.1f}-{hi:.1f}",
                "n": len(bucket),
                "avg_predicted": sum(p for p, _ in bucket) / len(bucket),
                "actual_rate": sum(o for _, o in bucket) / len(bucket),
            }
        )
    return rows
