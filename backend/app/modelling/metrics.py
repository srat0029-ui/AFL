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


def bias(predictions: list[float], actuals: list[float]) -> float:
    """Mean signed error (predicted - actual) — unlike mae/rmse this can be
    negative. Near zero means errors cancel out on average; a persistent
    positive or negative value means the model systematically over- or
    under-predicts, which mae alone can't reveal (a model that's +20 half
    the time and -20 the other half has the same mae as one that's
    consistently +0.5, but very different practical implications).
    """
    if not predictions:
        return float("nan")
    return sum(p - a for p, a in zip(predictions, actuals)) / len(predictions)


def beats_naive_baseline(model_metric: float, naive_metric: float, min_improvement: float = 0.05) -> bool:
    """True if `model_metric` (lower-is-better: Brier score, MAE, ...) beats
    `naive_metric` by at least `min_improvement` (relative). A model that
    barely edges out "always guess the average"/"always guess 50%" doesn't
    have a real, demonstrated edge in that market — this is the gate Stage
    1.5's confidence tiering checks before trusting any edge number.
    """
    if naive_metric <= 0:
        return False
    improvement = (naive_metric - model_metric) / naive_metric
    return improvement > min_improvement


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


# 50-55%, 55-60%, ..., 95-100% — finer near the decision boundary than
# calibration_table's 10 even bins, and folded onto the "favourite" side
# (see favourite_calibration_table) so a 90% prediction on either team
# lands in the same bucket instead of splitting evidence across two tails.
_FAVOURITE_BUCKET_EDGES = [0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0001]


def favourite_calibration_table(
    predictions: list[float], outcomes: list[float], bucket_edges: list[float] | None = None
) -> list[dict]:
    """Like calibration_table, but folded onto the side the model actually
    favoured: a prediction of 0.12 (away favoured at 88%) and one of 0.88
    (home favoured at 88%) both land in the "85-90%" bucket, scored against
    whether the favoured side actually won. This roughly doubles the
    effective sample size per bucket vs. bucketing raw home-win probability,
    and answers the question buyers of a probability actually care about:
    "when this model says 70%, does the favoured side win about 70% of the
    time?" — regardless of which team that happened to be.

    Draws contribute 0.5 to both "sides" and are handled by
    actual_home_outcome's existing 0.5 convention, so a draw partially
    counts against calibration in both directions rather than being
    dropped, matching how brier_score/log_loss already treat draws.
    """
    edges = bucket_edges or _FAVOURITE_BUCKET_EDGES
    buckets: list[list[tuple[float, float]]] = [[] for _ in range(len(edges) - 1)]

    for p, o in zip(predictions, outcomes):
        favourite_prob = max(p, 1 - p)
        favourite_won = o if p >= 0.5 else 1 - o
        for i in range(len(edges) - 1):
            if edges[i] <= favourite_prob < edges[i + 1]:
                buckets[i].append((favourite_prob, favourite_won))
                break

    rows = []
    for i, bucket in enumerate(buckets):
        lo, hi = edges[i], min(edges[i + 1], 1.0)
        label = f"{lo:.0%}-{hi:.0%}"
        if not bucket:
            rows.append({"bucket": label, "n": 0, "avg_predicted": None, "actual_rate": None})
            continue
        rows.append(
            {
                "bucket": label,
                "n": len(bucket),
                "avg_predicted": sum(p for p, _ in bucket) / len(bucket),
                "actual_rate": sum(o for _, o in bucket) / len(bucket),
            }
        )
    return rows


def expected_calibration_error(calibration_rows: list[dict]) -> float | None:
    """A single number summarising a calibration table: the sample-weighted
    average gap between predicted and actual rate across all non-empty
    buckets. 0.0 is perfect calibration; larger is worse. Standard "ECE"
    from the classifier-calibration literature, applied here to a
    calibration_table()/favourite_calibration_table() result.
    """
    populated = [row for row in calibration_rows if row["n"] > 0]
    total_n = sum(row["n"] for row in populated)
    if total_n == 0:
        return None
    return sum(row["n"] * abs(row["avg_predicted"] - row["actual_rate"]) for row in populated) / total_n
