import math

import pytest

from app.modelling.metrics import accuracy, brier_score, calibration_table, log_loss, mae, rmse


def test_brier_score_perfect_predictions_is_zero():
    assert brier_score([1.0, 0.0, 1.0], [1.0, 0.0, 1.0]) == pytest.approx(0.0)


def test_brier_score_always_half_against_coin_flips():
    assert brier_score([0.5, 0.5, 0.5, 0.5], [1.0, 0.0, 1.0, 0.0]) == pytest.approx(0.25)


def test_brier_score_empty_is_nan():
    assert math.isnan(brier_score([], []))


def test_log_loss_confident_correct_near_zero():
    assert log_loss([0.999999], [1.0]) < 0.001


def test_log_loss_confident_wrong_is_large():
    assert log_loss([0.999999], [0.0]) > 5


def test_log_loss_half_probability_is_ln2():
    assert log_loss([0.5], [1.0]) == pytest.approx(math.log(2), rel=1e-6)


def test_accuracy_counts_favourite_correctness():
    predictions = [0.7, 0.3, 0.9, 0.1]
    outcomes = [1.0, 1.0, 1.0, 0.0]  # correct, wrong, correct, correct
    assert accuracy(predictions, outcomes) == pytest.approx(0.75)


def test_accuracy_excludes_draws():
    predictions = [0.7, 0.6]
    outcomes = [1.0, 0.5]  # one win (correct), one draw (excluded)
    assert accuracy(predictions, outcomes) == pytest.approx(1.0)


def test_accuracy_empty_is_nan():
    assert math.isnan(accuracy([], []))


def test_calibration_table_buckets_and_aggregates():
    predictions = [0.15, 0.18, 0.55, 0.95]
    outcomes = [0.0, 1.0, 1.0, 1.0]

    table = calibration_table(predictions, outcomes, n_bins=10)

    bucket_01 = next(r for r in table if r["bucket"] == "0.1-0.2")
    assert bucket_01["n"] == 2
    assert bucket_01["avg_predicted"] == pytest.approx(0.165)
    assert bucket_01["actual_rate"] == pytest.approx(0.5)

    bucket_09 = next(r for r in table if r["bucket"] == "0.9-1.0")
    assert bucket_09["n"] == 1
    assert bucket_09["actual_rate"] == pytest.approx(1.0)

    bucket_00 = next(r for r in table if r["bucket"] == "0.0-0.1")
    assert bucket_00["n"] == 0
    assert bucket_00["avg_predicted"] is None


def test_calibration_table_prob_of_exactly_one_goes_in_last_bucket():
    table = calibration_table([1.0], [1.0], n_bins=10)
    bucket_09 = next(r for r in table if r["bucket"] == "0.9-1.0")
    assert bucket_09["n"] == 1


def test_mae_perfect_predictions_is_zero():
    assert mae([100.0, 150.0], [100.0, 150.0]) == pytest.approx(0.0)


def test_mae_known_value():
    assert mae([100.0, 160.0], [90.0, 150.0]) == pytest.approx(10.0)


def test_mae_empty_is_nan():
    assert math.isnan(mae([], []))


def test_rmse_penalises_large_errors_more_than_mae():
    predictions = [100.0, 100.0]
    actuals = [90.0, 130.0]  # errors of 10 and 30 -> same MAE=20 either way vs a uniform-20 case
    uniform_predictions = [100.0, 100.0]
    uniform_actuals = [80.0, 120.0]  # errors of 20 and 20 -> MAE also 20

    assert mae(predictions, actuals) == pytest.approx(mae(uniform_predictions, uniform_actuals))
    assert rmse(predictions, actuals) > rmse(uniform_predictions, uniform_actuals)


def test_rmse_empty_is_nan():
    assert math.isnan(rmse([], []))
