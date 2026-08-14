"""Poisson-based AFL scoring model — pure probability math.

A team's total points are modelled as 6*Goals + Behinds, where Goals and
Behinds are independent Poisson-distributed counts — not raw total points
treated as Poisson directly. This matters statistically: a Poisson(λ≈90)
fit to total points has variance ≈90 (std≈9.5), far too narrow for real AFL
scores (actual std is roughly 20-25 points). Goals (~10-20/game) and
behinds (~10-20/game) are individually Poisson-plausible; combining them as
6*G+B naturally produces realistic score variance (≈6²×15 + 15 ≈ 555,
std≈23.6). Using raw-points Poisson instead would silently produce
overconfident, miscalibrated total-points and margin probabilities.

Team strength here is a simple ratio model (attack/defense multipliers
relative to a rolling league average) — not a jointly-fit Dixon-Coles MLE.
That's the appropriate starting complexity; see poisson_backtest.py for how
ratios are computed walk-forward. Home and away scores are modelled
independently (no correlation term between the two teams' distributions) —
a known simplification vs. full Dixon-Coles, acceptable for a first version
and something later validation can justify revisiting.

Home-ground advantage is NOT a guessed multiplier here (an earlier version
tried that and a test caught it double-counting home advantage: boosting
the home team's expected score on top of ratings already pooled across both
home and away appearances systematically inflated predicted totals). It's
derived directly from data instead — see poisson_backtest.py's separate
rolling home/away league averages — so there's nothing about it to tune.
"""

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PoissonConfig:
    rolling_window_games: int = 44  # per-team form window, in games (~2 seasons at 22/season)
    min_games_for_reliable_strength: int = 6  # below this, shrink toward league-average (1.0) strength
    min_league_games_for_home_split: int = 40  # below this, shrink the home/away split toward neutral (1.0)
    max_goals: int = 30
    max_behinds: int = 30


def poisson_pmf(lam: float, k_max: int) -> np.ndarray:
    """P(X=k) for k in [0, k_max], X ~ Poisson(lam). Computed in log-space
    (via math.lgamma) for numerical stability at larger k, then renormalized
    to sum to 1 to account for the truncation at k_max."""
    lam = max(lam, 1e-6)
    ks = np.arange(0, k_max + 1)
    log_pmf = -lam + ks * math.log(lam) - np.array([math.lgamma(k + 1) for k in ks])
    pmf = np.exp(log_pmf)
    return pmf / pmf.sum()


def score_distribution(lambda_goals: float, lambda_behinds: float, max_goals: int, max_behinds: int) -> np.ndarray:
    """P(score=k) for k in [0, 6*max_goals + max_behinds], for score = 6*Goals + Behinds."""
    goals_pmf = poisson_pmf(lambda_goals, max_goals)
    behinds_pmf = poisson_pmf(lambda_behinds, max_behinds)

    max_score = 6 * max_goals + max_behinds
    score_pmf = np.zeros(max_score + 1)
    for goals, p_goals in enumerate(goals_pmf):
        if p_goals < 1e-10:
            continue
        start = 6 * goals
        score_pmf[start : start + max_behinds + 1] += p_goals * behinds_pmf
    return score_pmf


def total_points_distribution(home_pmf: np.ndarray, away_pmf: np.ndarray) -> np.ndarray:
    """P(home_score + away_score = k) for k starting at 0."""
    return np.convolve(home_pmf, away_pmf)


def margin_distribution(home_pmf: np.ndarray, away_pmf: np.ndarray) -> tuple[np.ndarray, int]:
    """Returns (pmf, min_margin): pmf[i] = P(home_score - away_score == min_margin + i)."""
    conv = np.convolve(home_pmf, away_pmf[::-1])
    min_margin = -(len(away_pmf) - 1)
    return conv, min_margin


def prob_total_over(home_pmf: np.ndarray, away_pmf: np.ndarray, line: float) -> float:
    total_pmf = total_points_distribution(home_pmf, away_pmf)
    ks = np.arange(len(total_pmf))
    return float(total_pmf[ks > line].sum())


def prob_margin_over(home_pmf: np.ndarray, away_pmf: np.ndarray, line: float) -> float:
    pmf, min_margin = margin_distribution(home_pmf, away_pmf)
    margins = np.arange(len(pmf)) + min_margin
    return float(pmf[margins > line].sum())


def win_draw_loss(home_pmf: np.ndarray, away_pmf: np.ndarray) -> tuple[float, float, float]:
    """Returns (P(home wins), P(draw), P(away wins)), derived from the margin distribution."""
    pmf, min_margin = margin_distribution(home_pmf, away_pmf)
    margins = np.arange(len(pmf)) + min_margin
    home_win = float(pmf[margins > 0].sum())
    draw = float(pmf[margins == 0].sum())
    away_win = float(pmf[margins < 0].sum())
    return home_win, draw, away_win


def expected_value(pmf: np.ndarray, offset: int = 0) -> float:
    return float(np.sum((np.arange(len(pmf)) + offset) * pmf))


def central_interval(pmf: np.ndarray, coverage: float, offset: int = 0) -> tuple[int, int]:
    """The narrowest [lower, upper] integer range (inclusive both ends,
    offset by `offset` — e.g. margin_distribution's negative minimum) whose
    cumulative probability is >= `coverage`, taken symmetrically from the
    two tails. Used to evaluate the model's full predicted distribution, not
    just its mean: if the model is well-calibrated, the actual outcome
    should fall inside its own 80% interval roughly 80% of the time.
    """
    if not 0.0 < coverage < 1.0:
        raise ValueError("coverage must be strictly between 0 and 1")
    tail = (1.0 - coverage) / 2.0
    cumulative = np.cumsum(pmf)
    lower_idx = int(np.searchsorted(cumulative, tail, side="left"))
    upper_idx = int(np.searchsorted(cumulative, 1.0 - tail, side="left"))
    lower_idx = min(lower_idx, len(pmf) - 1)
    upper_idx = min(max(upper_idx, lower_idx), len(pmf) - 1)
    return lower_idx + offset, upper_idx + offset
