"""Distribution modelling for disposal counts — turns a point prediction
(a mean) into a full ProjectionDistribution (app/player_modelling/
projection.py) that can answer P(disposals >= threshold) for arbitrary
thresholds, not just report the mean.

Two genuinely different approaches are implemented and compared on real
holdout data in disposal_backtest.py, rather than assuming either works a
priori:

1. NegativeBinomialDistribution — a parametric count distribution (mean mu,
   dispersion alpha, NB2 parametrisation: Var = mu + alpha*mu^2). Disposals
   are non-negative counts with variance that grows with the mean and is
   empirically larger than the mean itself (overdispersed relative to
   Poisson — see the disposal audit: mean~16, var~54 overall, and residual
   variance conditional on a point prediction is still well above the
   prediction itself for most players), which is exactly what a fixed
   dispersion parameter is meant to capture. Reuses the same
   log-space-PMF-then-renormalise technique as
   app/modelling/poisson_model.py's poisson_pmf, generalised to NB.

2. EmpiricalResidualDistribution — makes no distributional-family
   assumption at all: takes the empirical distribution of (actual -
   predicted) residuals observed on a held-out reference set, shifts it by
   this row's own predicted mean, and reads probabilities directly off
   that shifted empirical distribution (clipped at 0, since disposals can't
   be negative). Whichever of the two actually calibrates better on real
   holdout data (see disposal_backtest.py's distribution comparison) is
   what disposal_backtest.py selects as PRIMARY_DISTRIBUTION_METHOD - this
   module doesn't hardcode an assumption about which will win.

Deliberately NOT implemented as a default: disposals ~ Normal(mu, fixed_sd).
A fixed-sd Normal only reflects the AVERAGE spread across all rows, which
both understates the true spread for high-usage/high-variance players and
overstates it for very consistent ones — see disposal_backtest.py's
player-history-bucket analysis for the real evidence this matters.
"""

import math
from dataclasses import dataclass

import numpy as np
from scipy.special import gammaln

MAX_DISPOSALS = 60  # generous upper bound - the real dataset's max observed is 54 (see disposal audit)
_KS = np.arange(0, MAX_DISPOSALS + 1)  # shared across every call - avoids reallocating on every prediction
_LOG_FACTORIAL = gammaln(_KS + 1)  # precomputed once at import time


def nb_pmf(mu: float, alpha: float, k_max: int = MAX_DISPOSALS) -> np.ndarray:
    """P(X=k) for k in [0, k_max], X ~ NegativeBinomial with mean mu and
    variance mu + alpha*mu^2 (NB2 parametrisation; alpha=0 degenerates to
    Poisson). r = 1/alpha is the NB "size" parameter; computed in log-space
    for the same numerical-stability reason poisson_pmf uses it, then
    renormalised for the k_max truncation. Uses scipy.special.gammaln
    (vectorised, C-level) rather than a Python-level loop of math.lgamma per
    k - this function is called once per threshold per prediction across a
    real eval set of 60k+ rows, so a per-call Python loop over ~60 values
    was a real, measured bottleneck (see disposal_cli.py's backtest run)."""
    mu = max(mu, 1e-6)
    ks = _KS if k_max == MAX_DISPOSALS else np.arange(0, k_max + 1)
    log_factorial = _LOG_FACTORIAL if k_max == MAX_DISPOSALS else gammaln(ks + 1)

    if alpha <= 1e-9:
        # No overdispersion left to model - fall back to Poisson exactly
        # (avoids a division by ~0 in r = 1/alpha below).
        log_pmf = -mu + ks * math.log(mu) - log_factorial
        pmf = np.exp(log_pmf)
        return pmf / pmf.sum()

    r = 1.0 / alpha
    p = r / (r + mu)  # P(success), in the "number of failures before r successes" NB framing
    log_pmf = gammaln(ks + r) - gammaln(r) - log_factorial + r * math.log(p) + ks * math.log(1 - p)
    pmf = np.exp(log_pmf)
    return pmf / pmf.sum()


def expected_value(pmf: np.ndarray) -> float:
    return float(np.sum(np.arange(len(pmf)) * pmf))


def central_interval(pmf: np.ndarray, coverage: float) -> tuple[int, int]:
    """Same narrowest-symmetric-tail construction as
    app/modelling/poisson_model.py's central_interval, non-negative-only so
    no offset parameter is needed here."""
    if not 0.0 < coverage < 1.0:
        raise ValueError("coverage must be strictly between 0 and 1")
    tail = (1.0 - coverage) / 2.0
    cumulative = np.cumsum(pmf)
    lower_idx = int(np.searchsorted(cumulative, tail, side="left"))
    upper_idx = int(np.searchsorted(cumulative, 1.0 - tail, side="left"))
    lower_idx = min(lower_idx, len(pmf) - 1)
    upper_idx = min(max(upper_idx, lower_idx), len(pmf) - 1)
    return lower_idx, upper_idx


@dataclass(frozen=True)
class NegativeBinomialDistribution:
    """Satisfies app/player_modelling/projection.py's ProjectionDistribution
    protocol. `alpha` is typically fit once on training-period residuals
    (see disposal_models.py's fit_nb_dispersion) and reused for every row -
    a single global dispersion, not fit per-player (too little per-player
    data to estimate variance reliably for most players; see the
    player-history-bucket findings for why this is an acceptable
    simplification, not a free assumption)."""

    mu: float
    alpha: float

    def _pmf(self) -> np.ndarray:
        return nb_pmf(self.mu, self.alpha)

    def mean(self) -> float:
        return self.mu

    def prob_at_least(self, threshold: float) -> float:
        pmf = self._pmf()
        k_min = math.ceil(threshold)
        if k_min <= 0:
            return 1.0
        if k_min >= len(pmf):
            return 0.0
        return float(pmf[k_min:].sum())

    def prob_over(self, line: float) -> float:
        return self.prob_at_least(math.floor(line) + 1)

    def median(self) -> float:
        pmf = self._pmf()
        cumulative = np.cumsum(pmf)
        return float(np.searchsorted(cumulative, 0.5, side="left"))

    def interval(self, coverage: float) -> tuple[int, int]:
        return central_interval(self._pmf(), coverage)


@dataclass(frozen=True)
class EmpiricalResidualDistribution:
    """Non-parametric alternative: `residual_quantiles` is a sorted array of
    (actual - predicted) values observed on a reference set (e.g. the
    training/tune period), assumed exchangeable across rows with the same
    predicted mean (a genuine simplification — see disposal_backtest.py for
    whether validation supports it). This row's own predicted `mu` shifts
    that empirical residual distribution; disposals below 0 are clipped to
    0 rather than allowed to go negative."""

    mu: float
    # A pre-sorted-ascending numpy array, not a tuple - deliberately: this
    # SAME array object is shared (by reference, not copied) across every
    # PredictionRecord built from one model fit (see disposal_backtest.py's
    # _predict_and_wrap), so re-converting it from a tuple on every single
    # prob_at_least() call doesn't repeat an O(n) tuple->array conversion
    # for every one of tens of thousands of predictions - a real measured
    # bottleneck once the O(n) linear scan itself was fixed (see
    # prob_at_least's docstring below).
    sorted_residuals: np.ndarray

    def _sorted_array(self) -> np.ndarray:
        return self.sorted_residuals

    def mean(self) -> float:
        return self.mu

    def prob_at_least(self, threshold: float) -> float:
        if threshold <= 0:
            return 1.0  # every sample is >= 0 after clipping, regardless of residual
        arr = self._sorted_array()
        idx = np.searchsorted(arr, threshold - self.mu, side="left")
        return float(len(arr) - idx) / len(arr)

    def prob_over(self, line: float) -> float:
        if line < 0:
            return 1.0
        arr = self._sorted_array()
        idx = np.searchsorted(arr, line - self.mu, side="right")
        return float(len(arr) - idx) / len(arr)

    def median(self) -> float:
        arr = self._sorted_array()
        idx = int(round(0.5 * (len(arr) - 1)))
        return max(float(arr[idx]) + self.mu, 0.0)

    def interval(self, coverage: float) -> tuple[float, float]:
        arr = self._sorted_array()
        n = len(arr)
        tail = (1.0 - coverage) / 2.0
        lo_idx = int(round(tail * (n - 1)))
        hi_idx = int(round((1.0 - tail) * (n - 1)))
        lo = max(float(arr[lo_idx]) + self.mu, 0.0)
        hi = max(float(arr[hi_idx]) + self.mu, 0.0)
        return lo, hi
