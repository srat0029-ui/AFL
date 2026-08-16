"""Tests for goal_distribution.py — NB and Hurdle distribution math.
Section 27's "count-distribution probabilities", "monotonic threshold
probabilities", "probability sums", and "zero inflation/hurdle
calculations".
"""

import pytest

from app.player_modelling.goal_distribution import HurdleDistribution, NegativeBinomialGoalDistribution


def test_nb_goal_pmf_sums_to_one():
    d = NegativeBinomialGoalDistribution(mu=0.5, alpha=1.5)
    total = sum(d.pmf_at(k) for k in range(16))
    assert total == pytest.approx(1.0, abs=1e-6)


def test_nb_goal_mean_matches_mu():
    d = NegativeBinomialGoalDistribution(mu=0.8, alpha=1.5)
    mean = sum(k * d.pmf_at(k) for k in range(16))
    assert mean == pytest.approx(0.8, abs=0.01)


def test_nb_goal_thresholds_are_monotonically_decreasing():
    d = NegativeBinomialGoalDistribution(mu=1.5, alpha=1.5)
    p1, p2, p3, p4 = d.prob_at_least(1), d.prob_at_least(2), d.prob_at_least(3), d.prob_at_least(4)
    assert p1 >= p2 >= p3 >= p4 >= 0


def test_nb_goal_prob_over_half_line_equals_next_integer_at_least():
    d = NegativeBinomialGoalDistribution(mu=1.2, alpha=1.5)
    assert d.prob_over(1.5) == pytest.approx(d.prob_at_least(2), abs=1e-9)
    assert d.prob_over(2.5) == pytest.approx(d.prob_at_least(3), abs=1e-9)


# --- Hurdle model ---


def test_hurdle_pmf_sums_to_one():
    h = HurdleDistribution(p_score=0.4, mu_scored=1.8, alpha_scored=1.0)
    total = sum(h.pmf_at(k) for k in range(16))
    assert total == pytest.approx(1.0, abs=1e-6)


def test_hurdle_p_zero_exactly_matches_one_minus_p_score():
    h = HurdleDistribution(p_score=0.35, mu_scored=1.5, alpha_scored=1.0)
    assert h.pmf_at(0) == pytest.approx(0.65, abs=1e-9)


def test_hurdle_prob_at_least_one_exactly_matches_p_score():
    h = HurdleDistribution(p_score=0.35, mu_scored=1.5, alpha_scored=1.0)
    assert h.prob_at_least(1) == pytest.approx(0.35, abs=1e-9)


def test_hurdle_thresholds_are_monotonically_decreasing():
    h = HurdleDistribution(p_score=0.6, mu_scored=2.5, alpha_scored=1.2)
    p1, p2, p3, p4, p5 = (h.prob_at_least(k) for k in (1, 2, 3, 4, 5))
    assert p1 >= p2 >= p3 >= p4 >= p5 >= 0
    assert p1 == pytest.approx(0.6, abs=1e-9)


def test_hurdle_prob_at_least_zero_is_one():
    h = HurdleDistribution(p_score=0.5, mu_scored=1.0, alpha_scored=1.0)
    assert h.prob_at_least(0) == 1.0


def test_hurdle_mean_is_between_zero_and_p_score_times_a_large_bound():
    h = HurdleDistribution(p_score=0.4, mu_scored=2.0, alpha_scored=1.0)
    mean = h.mean()
    assert 0 < mean < 5  # sane bound - a mean of 0.4*~2 conditional mean, roughly ~0.8-1.0


def test_hurdle_interval_is_ordered():
    h = HurdleDistribution(p_score=0.5, mu_scored=2.0, alpha_scored=1.0)
    lo, hi = h.interval(0.8)
    assert lo <= hi


def test_hurdle_with_zero_p_score_is_a_point_mass_at_zero():
    h = HurdleDistribution(p_score=0.0, mu_scored=3.0, alpha_scored=1.0)
    assert h.pmf_at(0) == pytest.approx(1.0, abs=1e-6)
    assert h.prob_at_least(1) == pytest.approx(0.0, abs=1e-6)


def test_hurdle_with_p_score_near_one_rarely_predicts_zero():
    h = HurdleDistribution(p_score=0.999, mu_scored=2.0, alpha_scored=1.0)
    assert h.pmf_at(0) == pytest.approx(0.001, abs=1e-6)
