"""Distribution modelling for goal counts — Section 7-8 of the goal-
prediction stage brief. The audit (see goal_cli.py's real output) found
genuine, material zero-inflation: actual P(0)=66.8% vs a plain Poisson
fit to the same mean predicting only 58.8% - about 14% more zeros than
Poisson alone explains - plus clear overdispersion (variance/mean ~1.66).
Two distribution families are implemented and compared on real holdout
data, not assumed:

1. NegativeBinomialDistribution - reused as-is from disposal_distribution.py
   (same NB2 math; goals are still non-negative counts). Handles
   overdispersion but treats zero as just "count = 0 under the same
   process as every other count" - no separate zero mechanism.

2. HurdleDistribution - a genuine two-part model, the brief's suggested
   architecture for exactly this situation: P(scores at least once) is
   modelled as its own probability `p_score` (fit as a separate binary
   classifier - see goal_models.py's fit_hurdle_model), and conditional on
   scoring, goals follow a NEGATIVE-BINOMIAL renormalised to exclude its
   own zero mass (a zero-truncated NB). This gives P(Y=0) = 1 - p_score
   EXACTLY, rather than whatever a single count model's fitted mean
   happens to imply - directly addressing the audited zero-inflation.

Zero-inflated Poisson/NB (a MIXTURE model - some zeros are "structural",
the rest come from the same count process as positive values) was
considered but not implemented: it requires jointly optimising a mixture
likelihood (more fragile to fit reliably) for a similar underlying idea to
the hurdle model - which fits two independent, simpler, well-understood
pieces (a logistic classifier + a truncated count model) and, per the
brief's own instruction not to add complexity unless it improves held-out
metrics, is tested against plain NB on real data before deciding whether
the extra zero-handling machinery earns its place (see
goal_analysis.compare_hurdle_vs_nb).
"""

import math
from dataclasses import dataclass

import numpy as np

from app.player_modelling.disposal_distribution import nb_pmf

MAX_GOALS = 15  # generous upper bound - the real dataset's max observed is 11 (see goal audit)


@dataclass(frozen=True)
class NegativeBinomialGoalDistribution:
    """Same NB2 math as disposal_distribution.NegativeBinomialDistribution,
    re-parametrised for goals' much smaller scale (mean ~0.5, max~11) via
    MAX_GOALS instead of MAX_DISPOSALS."""

    mu: float
    alpha: float

    def _pmf(self) -> np.ndarray:
        return nb_pmf(self.mu, self.alpha, k_max=MAX_GOALS)

    def mean(self) -> float:
        return self.mu

    def pmf_at(self, k: int) -> float:
        pmf = self._pmf()
        return float(pmf[k]) if 0 <= k < len(pmf) else 0.0

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

    def interval(self, coverage: float) -> tuple[int, int]:
        from app.player_modelling.disposal_distribution import central_interval

        return central_interval(self._pmf(), coverage)


@dataclass(frozen=True)
class HurdleDistribution:
    """Two-part discrete distribution over {0, 1, 2, ...}:
      P(Y=0) = 1 - p_score
      P(Y=k) = p_score * NB(k; mu_scored, alpha_scored) / (1 - NB(0; mu_scored, alpha_scored))   for k >= 1

    `mu_scored`/`alpha_scored` describe the NB fit ONLY on rows where the
    player scored at least once (see goal_models.fit_hurdle_model) -
    dividing by (1 - NB(0)) zero-truncates that distribution so the k>=1
    probabilities sum to exactly p_score, keeping the whole thing a valid
    distribution (verified in tests/test_goal_distribution.py)."""

    p_score: float
    mu_scored: float
    alpha_scored: float

    def _pmf(self) -> np.ndarray:
        raw = nb_pmf(self.mu_scored, self.alpha_scored, k_max=MAX_GOALS)
        p0_raw = float(raw[0])
        truncated_tail = raw[1:] / max(1.0 - p0_raw, 1e-9)  # renormalise k>=1 to sum to 1
        pmf = np.zeros(MAX_GOALS + 1)
        pmf[0] = 1.0 - self.p_score
        pmf[1:] = self.p_score * truncated_tail
        # Guard against floating-point drift so the distribution always sums to 1 exactly.
        pmf = pmf / pmf.sum()
        return pmf

    def mean(self) -> float:
        pmf = self._pmf()
        return float(np.sum(np.arange(len(pmf)) * pmf))

    def pmf_at(self, k: int) -> float:
        pmf = self._pmf()
        return float(pmf[k]) if 0 <= k < len(pmf) else 0.0

    def prob_at_least(self, threshold: float) -> float:
        k_min = math.ceil(threshold)
        if k_min <= 0:
            return 1.0
        pmf = self._pmf()
        if k_min >= len(pmf):
            return 0.0
        return float(pmf[k_min:].sum())

    def prob_over(self, line: float) -> float:
        return self.prob_at_least(math.floor(line) + 1)

    def interval(self, coverage: float) -> tuple[int, int]:
        from app.player_modelling.disposal_distribution import central_interval

        return central_interval(self._pmf(), coverage)
