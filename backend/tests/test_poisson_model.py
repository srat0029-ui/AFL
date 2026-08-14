import math

import numpy as np
import pytest

from app.modelling.poisson_model import (
    central_interval,
    expected_value,
    margin_distribution,
    poisson_pmf,
    prob_margin_over,
    prob_total_over,
    score_distribution,
    total_points_distribution,
    win_draw_loss,
)


def test_poisson_pmf_matches_known_values():
    pmf = poisson_pmf(lam=2.0, k_max=10)
    assert pmf[0] == pytest.approx(math.exp(-2), rel=1e-4)
    assert pmf[1] == pytest.approx(2 * math.exp(-2), rel=1e-4)


def test_poisson_pmf_sums_to_one():
    pmf = poisson_pmf(lam=15.0, k_max=30)
    assert pmf.sum() == pytest.approx(1.0)


def test_score_distribution_sums_to_one_and_has_correct_mean():
    pmf = score_distribution(lambda_goals=13.0, lambda_behinds=15.0, max_goals=30, max_behinds=30)
    assert pmf.sum() == pytest.approx(1.0, abs=1e-6)
    assert expected_value(pmf) == pytest.approx(6 * 13.0 + 15.0, abs=0.5)


def test_score_distribution_variance_much_larger_than_naive_points_poisson():
    """The whole point of the goals+behinds decomposition: realistic score
    variance, not the far-too-narrow variance a direct Poisson(90) would give."""
    pmf = score_distribution(lambda_goals=13.0, lambda_behinds=15.0, max_goals=30, max_behinds=30)
    mean = expected_value(pmf)
    ks = np.arange(len(pmf))
    variance = float(np.sum(pmf * (ks - mean) ** 2))
    std = variance**0.5

    naive_poisson_std = mean**0.5  # if points were modelled as a single Poisson(mean)
    assert std > 2 * naive_poisson_std  # decomposed model has much more realistic spread
    assert 18 < std < 30  # roughly matches real AFL score variability


def test_margin_distribution_offset_is_correct_hand_verified_case():
    """home deterministically scores 2, away deterministically scores 1 —
    margin must land exactly on +1 with the right array offset."""
    home_pmf = np.array([0.0, 0.0, 1.0])  # P(home=2) = 1
    away_pmf = np.array([0.0, 1.0])  # P(away=1) = 1

    pmf, min_margin = margin_distribution(home_pmf, away_pmf)
    margins = np.arange(len(pmf)) + min_margin

    winning_margin = margins[np.argmax(pmf)]
    assert winning_margin == 1
    assert pmf[np.argmax(pmf)] == pytest.approx(1.0)


def test_total_points_distribution_hand_verified_case():
    home_pmf = np.array([0.0, 0.0, 1.0])  # home=2
    away_pmf = np.array([0.0, 1.0])  # away=1

    total_pmf = total_points_distribution(home_pmf, away_pmf)
    winning_total = np.argmax(total_pmf)

    assert winning_total == 3  # 2 + 1
    assert total_pmf[winning_total] == pytest.approx(1.0)


def test_margin_expected_value_matches_difference_of_means():
    home_pmf = score_distribution(14.0, 15.0, 30, 30)
    away_pmf = score_distribution(12.0, 14.0, 30, 30)

    pmf, min_margin = margin_distribution(home_pmf, away_pmf)
    margin_mean = expected_value(pmf, offset=min_margin)

    expected_margin = expected_value(home_pmf) - expected_value(away_pmf)
    assert margin_mean == pytest.approx(expected_margin, abs=0.5)


def test_win_draw_loss_sums_to_one():
    home_pmf = score_distribution(13.0, 15.0, 30, 30)
    away_pmf = score_distribution(11.0, 13.0, 30, 30)
    home_win, draw, away_win = win_draw_loss(home_pmf, away_pmf)

    assert home_win + draw + away_win == pytest.approx(1.0, abs=1e-6)
    assert draw < 0.05  # draws are rare in AFL; a sane model shouldn't predict them often


def test_win_draw_loss_favours_stronger_team():
    strong_pmf = score_distribution(18.0, 18.0, 30, 30)
    weak_pmf = score_distribution(9.0, 9.0, 30, 30)

    home_win, _, away_win = win_draw_loss(strong_pmf, weak_pmf)
    assert home_win > away_win
    assert home_win > 0.8


def test_win_draw_loss_symmetric_for_identical_distributions():
    pmf = score_distribution(13.0, 15.0, 30, 30)
    home_win, draw, away_win = win_draw_loss(pmf, pmf)
    assert home_win == pytest.approx(away_win, abs=1e-9)


def test_prob_total_over_is_monotonic_decreasing_in_line():
    home_pmf = score_distribution(13.0, 15.0, 30, 30)
    away_pmf = score_distribution(12.0, 14.0, 30, 30)

    p_low = prob_total_over(home_pmf, away_pmf, 100.0)
    p_mid = prob_total_over(home_pmf, away_pmf, 160.0)
    p_high = prob_total_over(home_pmf, away_pmf, 220.0)

    assert p_low > p_mid > p_high
    assert 0.0 <= p_high <= p_mid <= p_low <= 1.0


def test_prob_margin_over_zero_matches_win_probability():
    home_pmf = score_distribution(14.0, 15.0, 30, 30)
    away_pmf = score_distribution(11.0, 13.0, 30, 30)

    home_win, _, _ = win_draw_loss(home_pmf, away_pmf)
    prob_margin_over_zero = prob_margin_over(home_pmf, away_pmf, 0.0)

    assert prob_margin_over_zero == pytest.approx(home_win, abs=1e-6)


def test_central_interval_contains_the_stated_coverage():
    pmf = score_distribution(13.0, 15.0, 30, 30)
    total_pmf = total_points_distribution(pmf, pmf)
    lo, hi = central_interval(total_pmf, coverage=0.8)

    covered = total_pmf[lo : hi + 1].sum()
    assert covered >= 0.8
    # and shouldn't be dramatically wider than necessary (loose sanity bound)
    assert covered < 0.95


def test_central_interval_widens_with_higher_coverage():
    pmf = score_distribution(13.0, 15.0, 30, 30)
    lo_50, hi_50 = central_interval(pmf, coverage=0.5)
    lo_90, hi_90 = central_interval(pmf, coverage=0.9)

    assert (hi_90 - lo_90) > (hi_50 - lo_50)


def test_central_interval_is_centred_on_the_mean_for_a_symmetric_pmf():
    pmf = poisson_pmf(lam=50.0, k_max=150)  # large lambda -> roughly symmetric
    mean = expected_value(pmf)
    lo, hi = central_interval(pmf, coverage=0.8)

    assert lo < mean < hi


def test_central_interval_respects_offset():
    home_pmf = score_distribution(13.0, 15.0, 30, 30)
    away_pmf = score_distribution(12.0, 14.0, 30, 30)
    margin_pmf, min_margin = margin_distribution(home_pmf, away_pmf)

    lo, hi = central_interval(margin_pmf, coverage=0.8, offset=min_margin)

    # without the offset, indices would start at 0 — with it, they should
    # be shifted into plausible real AFL-margin territory (roughly -150..150)
    assert -200 < lo < 200
    assert -200 < hi < 200
    assert lo < hi


def test_central_interval_rejects_invalid_coverage():
    pmf = poisson_pmf(lam=10.0, k_max=30)
    with pytest.raises(ValueError):
        central_interval(pmf, coverage=1.5)
    with pytest.raises(ValueError):
        central_interval(pmf, coverage=0.0)
