"""Match-level bootstrap resampling for estimating uncertainty in a
model-vs-model metric difference.

A single point-estimate improvement ("Brier improved by 0.006") says
nothing about whether that's a real, repeatable effect or noise from which
1,431 particular matches happened to be evaluated. Resampling matches with
replacement and recomputing the same metric difference many times gives a
distribution of plausible differences under the data actually observed —
its 2.5th/97.5th percentiles form a 95% interval. If that interval contains
zero, the improvement is not distinguishable from noise at that confidence
level, and should be reported as such rather than declared a win.
"""

import random
from dataclasses import dataclass
from typing import Callable

MetricFn = Callable[[list[float], list[float]], float]


@dataclass(frozen=True)
class BootstrapResult:
    point_estimate: float  # metric_a - metric_b on the real (non-resampled) data
    ci_low: float
    ci_high: float
    n_resamples: int

    @property
    def excludes_zero(self) -> bool:
        """True if the interval doesn't straddle zero — i.e. the sign of
        the difference is consistent across resamples, not just the point estimate."""
        return self.ci_low > 0 or self.ci_high < 0


def bootstrap_metric_difference(
    probs_a: list[float],
    probs_b: list[float],
    outcomes: list[float],
    metric_fn: MetricFn,
    n_resamples: int = 2000,
    seed: int = 42,
) -> BootstrapResult:
    """probs_a/probs_b/outcomes must be the same length, aligned to the
    identical match set (index i in each list is the same match). Returns
    a distribution of (metric(a) - metric(b)) under resampling; deterministic
    given the same inputs and seed.
    """
    n = len(outcomes)
    if n == 0 or len(probs_a) != n or len(probs_b) != n:
        raise ValueError("probs_a, probs_b, and outcomes must be the same non-zero length")

    point_a = metric_fn(probs_a, outcomes)
    point_b = metric_fn(probs_b, outcomes)
    point_diff = point_a - point_b

    rng = random.Random(seed)
    indices = list(range(n))
    diffs = []
    for _ in range(n_resamples):
        sample_idx = rng.choices(indices, k=n)
        sample_a = [probs_a[i] for i in sample_idx]
        sample_b = [probs_b[i] for i in sample_idx]
        sample_outcomes = [outcomes[i] for i in sample_idx]
        diffs.append(metric_fn(sample_a, sample_outcomes) - metric_fn(sample_b, sample_outcomes))

    diffs.sort()
    lo_idx = int(0.025 * n_resamples)
    hi_idx = min(int(0.975 * n_resamples), n_resamples - 1)

    return BootstrapResult(point_estimate=point_diff, ci_low=diffs[lo_idx], ci_high=diffs[hi_idx], n_resamples=n_resamples)
