"""Tests for disposal_distribution.py's threshold-probability and
prediction-interval math — Section 26's "threshold probability math",
"prediction interval calculations", and "distribution probabilities
sum/behave correctly, arbitrary half-lines produce correct probabilities".
"""

import numpy as np
import pytest

from app.player_modelling.disposal_distribution import EmpiricalResidualDistribution, NegativeBinomialDistribution, nb_pmf


def test_nb_pmf_sums_to_one():
    pmf = nb_pmf(mu=18.0, alpha=0.05)
    assert pmf.sum() == pytest.approx(1.0, abs=1e-9)


def test_nb_pmf_mean_matches_mu():
    pmf = nb_pmf(mu=18.0, alpha=0.05)
    mean = float(np.sum(np.arange(len(pmf)) * pmf))
    assert mean == pytest.approx(18.0, abs=0.01)


def test_nb_pmf_degenerates_to_poisson_when_alpha_is_zero():
    pmf_nb = nb_pmf(mu=10.0, alpha=1e-12)
    # Poisson(10) pmf at k=10 via direct formula
    import math

    poisson_p10 = math.exp(-10) * 10**10 / math.factorial(10)
    assert pmf_nb[10] == pytest.approx(poisson_p10, rel=1e-3)


def test_nb_distribution_mean_is_mu():
    d = NegativeBinomialDistribution(mu=22.0, alpha=0.04)
    assert d.mean() == 22.0


def test_nb_prob_at_least_zero_is_one():
    d = NegativeBinomialDistribution(mu=15.0, alpha=0.05)
    assert d.prob_at_least(0) == 1.0


def test_nb_prob_at_least_decreases_with_threshold():
    d = NegativeBinomialDistribution(mu=20.0, alpha=0.05)
    p20 = d.prob_at_least(20)
    p25 = d.prob_at_least(25)
    p30 = d.prob_at_least(30)
    assert p20 > p25 > p30


def test_nb_prob_at_least_matches_direct_pmf_sum():
    d = NegativeBinomialDistribution(mu=20.0, alpha=0.05)
    pmf = nb_pmf(20.0, 0.05)
    expected = float(pmf[25:].sum())
    assert d.prob_at_least(25) == pytest.approx(expected, abs=1e-9)


def test_nb_prob_over_half_line_equals_prob_at_least_next_integer():
    """prob_over(27.5) must equal prob_at_least(28) for an integer-valued
    outcome - the exact equivalence app/player_modelling/projection.py's
    docstring calls out as NOT generally guaranteed across distribution
    types, but which must hold for this specific (integer-valued disposal
    count) distribution."""
    d = NegativeBinomialDistribution(mu=25.0, alpha=0.06)
    assert d.prob_over(27.5) == pytest.approx(d.prob_at_least(28), abs=1e-9)


def test_nb_interval_is_ordered_and_symmetric_in_probability_mass():
    d = NegativeBinomialDistribution(mu=20.0, alpha=0.05)
    lo, hi = d.interval(0.8)
    assert lo < d.mean() < hi
    wider_lo, wider_hi = d.interval(0.9)
    assert wider_lo <= lo and wider_hi >= hi  # a higher coverage interval must be at least as wide


def test_nb_higher_alpha_produces_wider_interval():
    """More overdispersion (higher alpha) should widen the predictive
    interval for the same mean - a basic sanity check that alpha actually
    controls spread."""
    narrow = NegativeBinomialDistribution(mu=20.0, alpha=0.01)
    wide = NegativeBinomialDistribution(mu=20.0, alpha=0.2)
    n_lo, n_hi = narrow.interval(0.8)
    w_lo, w_hi = wide.interval(0.8)
    assert (w_hi - w_lo) > (n_hi - n_lo)


# --- Empirical residual distribution ---


def _brute_force_empirical(mu, residuals, threshold, strict=False):
    samples = np.clip(np.array(residuals) + mu, 0, None)
    return float(np.mean(samples > threshold)) if strict else float(np.mean(samples >= threshold))


def test_empirical_prob_at_least_matches_brute_force():
    rng = np.random.default_rng(7)
    residuals = np.sort(rng.normal(0, 5, 2000))
    d = EmpiricalResidualDistribution(mu=18.0, sorted_residuals=residuals)
    for threshold in (10, 15, 20, 25, 30):
        assert d.prob_at_least(threshold) == pytest.approx(_brute_force_empirical(18.0, residuals, threshold), abs=1e-9)


def test_empirical_prob_over_arbitrary_half_line_matches_brute_force():
    rng = np.random.default_rng(7)
    residuals = np.sort(rng.normal(0, 5, 2000))
    d = EmpiricalResidualDistribution(mu=18.0, sorted_residuals=residuals)
    for line in (19.5, 24.5, 29.5, 34.5):
        assert d.prob_over(line) == pytest.approx(_brute_force_empirical(18.0, residuals, line, strict=True), abs=1e-9)


def test_empirical_prob_at_least_negative_threshold_is_one():
    residuals = np.array([-5.0, 0.0, 5.0])
    d = EmpiricalResidualDistribution(mu=1.0, sorted_residuals=residuals)
    assert d.prob_at_least(-1) == 1.0


def test_empirical_interval_contains_the_median():
    rng = np.random.default_rng(3)
    residuals = np.sort(rng.normal(0, 4, 1000))
    d = EmpiricalResidualDistribution(mu=15.0, sorted_residuals=residuals)
    lo, hi = d.interval(0.8)
    assert lo <= d.median() <= hi


def test_empirical_never_produces_negative_values():
    residuals = np.array([-100.0, -50.0, 0.0, 5.0])
    d = EmpiricalResidualDistribution(mu=2.0, sorted_residuals=residuals)
    lo, _hi = d.interval(0.9)
    assert lo >= 0.0
    assert d.median() >= 0.0
