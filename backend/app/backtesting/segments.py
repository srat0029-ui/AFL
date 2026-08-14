"""Generic grouping + metric aggregation for backtest reports.

Works on anything with `.home_win_probability` / `.actual_home_outcome` /
`.home_team_id` / `.away_team_id` / `.season_year` — both EloPrediction and
PoissonPrediction have these fields by design (see
app/modelling/elo_backtest.py and app/modelling/poisson_backtest.py), so the
same segmenting logic serves both models without duplication.
"""

from dataclasses import dataclass
from typing import Callable

from app.modelling.metrics import accuracy, brier_score, log_loss, mae


@dataclass(frozen=True)
class BacktestSegment:
    label: str
    n: int
    metrics: dict[str, float]


def win_prob_metrics_from_pairs(probs: list[float], outcomes: list[float]) -> dict[str, float]:
    if not probs:
        return {}
    return {
        "brier_score": brier_score(probs, outcomes),
        "log_loss": log_loss(probs, outcomes),
        "accuracy": accuracy(probs, outcomes),
    }


def win_prob_metrics(predictions: list) -> dict[str, float]:
    return win_prob_metrics_from_pairs(
        [p.home_win_probability for p in predictions], [p.actual_home_outcome for p in predictions]
    )


def scoring_metrics(predictions: list) -> dict[str, float]:
    if not predictions:
        return {}
    total_pred = [p.expected_total_points for p in predictions]
    total_actual = [p.actual_total_points for p in predictions]
    margin_pred = [p.expected_margin for p in predictions]
    margin_actual = [p.actual_margin for p in predictions]
    return {
        "total_points_mae": mae(total_pred, total_actual),
        "margin_mae": mae(margin_pred, margin_actual),
    }


def build_segments(predictions: list, key_fn: Callable, metrics_fn: Callable) -> list[BacktestSegment]:
    """Groups `predictions` by key_fn(prediction) -> label, computes
    metrics_fn on each group, returns non-empty segments sorted by label."""
    groups: dict[str, list] = {}
    for p in predictions:
        groups.setdefault(key_fn(p), []).append(p)

    segments = [
        BacktestSegment(label=label, n=len(items), metrics=metrics_fn(items)) for label, items in groups.items() if items
    ]
    return sorted(segments, key=lambda s: s.label)


_CONVICTION_BINS = [(0.5, 0.6, "50-60%"), (0.6, 0.7, "60-70%"), (0.7, 0.85, "70-85%"), (0.85, 1.01, "85-100%")]


def conviction_bucket(home_win_probability: float) -> str:
    """How sure the model was, regardless of which side it favoured — e.g. a
    prediction of 0.15 and one of 0.85 both land in "85-100%". Not the same
    thing as the live confidence tiers in app/edges/confidence.py, which
    factor in market data this backtest doesn't have — this is a proxy
    using only the model's own stated probability, labelled as such
    everywhere it's surfaced.
    """
    favourite_prob = max(home_win_probability, 1 - home_win_probability)
    for lo, hi, label in _CONVICTION_BINS:
        if lo <= favourite_prob < hi:
            return label
    return _CONVICTION_BINS[-1][2]


def team_perspective(prediction, team_id: int) -> tuple[float, float] | None:
    """Returns (that team's own win probability, that team's own outcome)
    regardless of whether they were home or away — needed for a fair "by
    team" breakdown, since a team's games are split across both sides.
    """
    if prediction.home_team_id == team_id:
        return prediction.home_win_probability, prediction.actual_home_outcome
    if prediction.away_team_id == team_id:
        return 1.0 - prediction.home_win_probability, 1.0 - prediction.actual_home_outcome
    return None
