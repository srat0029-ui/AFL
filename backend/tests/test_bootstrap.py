import pytest

from app.modelling.bootstrap import bootstrap_metric_difference
from app.modelling.metrics import brier_score


def test_point_estimate_matches_direct_metric_difference():
    probs_a = [0.6, 0.7, 0.4, 0.55, 0.8]
    probs_b = [0.5, 0.6, 0.5, 0.5, 0.6]
    outcomes = [1.0, 1.0, 0.0, 1.0, 1.0]

    result = bootstrap_metric_difference(probs_a, probs_b, outcomes, brier_score)

    expected = brier_score(probs_a, outcomes) - brier_score(probs_b, outcomes)
    assert result.point_estimate == pytest.approx(expected)


def test_identical_predictions_give_zero_interval():
    probs = [0.6, 0.4, 0.7, 0.3, 0.55, 0.65, 0.45, 0.5, 0.6, 0.4]
    outcomes = [1.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 0.0, 1.0, 0.0]

    result = bootstrap_metric_difference(probs, probs, outcomes, brier_score, n_resamples=500)

    assert result.point_estimate == pytest.approx(0.0)
    assert result.ci_low == pytest.approx(0.0)
    assert result.ci_high == pytest.approx(0.0)
    assert not result.excludes_zero


def test_clearly_better_model_gives_interval_excluding_zero():
    # model A near-perfect, model B near-random, over many matches -> the
    # improvement should be consistent across virtually all resamples
    n = 300
    outcomes = [1.0 if i % 2 == 0 else 0.0 for i in range(n)]
    probs_a = [0.95 if o == 1.0 else 0.05 for o in outcomes]  # excellent
    probs_b = [0.5 for _ in outcomes]  # uninformative

    result = bootstrap_metric_difference(probs_a, probs_b, outcomes, brier_score, n_resamples=500)

    assert result.point_estimate < 0  # A's brier is much lower (better) than B's
    assert result.excludes_zero


def test_deterministic_given_same_seed():
    probs_a = [0.6, 0.4, 0.7, 0.3, 0.55] * 10
    probs_b = [0.5, 0.5, 0.6, 0.4, 0.5] * 10
    outcomes = [1.0, 0.0, 1.0, 0.0, 1.0] * 10

    result_1 = bootstrap_metric_difference(probs_a, probs_b, outcomes, brier_score, n_resamples=300, seed=123)
    result_2 = bootstrap_metric_difference(probs_a, probs_b, outcomes, brier_score, n_resamples=300, seed=123)

    assert result_1 == result_2


def test_different_seeds_can_give_different_intervals():
    probs_a = [0.6, 0.4, 0.7, 0.3, 0.55] * 10
    probs_b = [0.5, 0.5, 0.6, 0.4, 0.5] * 10
    outcomes = [1.0, 0.0, 1.0, 0.0, 1.0] * 10

    result_1 = bootstrap_metric_difference(probs_a, probs_b, outcomes, brier_score, n_resamples=300, seed=1)
    result_2 = bootstrap_metric_difference(probs_a, probs_b, outcomes, brier_score, n_resamples=300, seed=2)

    # point estimates are identical (not resampling-dependent) but the
    # resampled interval bounds may differ slightly between seeds
    assert result_1.point_estimate == result_2.point_estimate


def test_raises_on_mismatched_lengths():
    with pytest.raises(ValueError):
        bootstrap_metric_difference([0.5, 0.6], [0.5], [1.0, 0.0], brier_score)


def test_raises_on_empty_input():
    with pytest.raises(ValueError):
        bootstrap_metric_difference([], [], [], brier_score)
