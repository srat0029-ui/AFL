import math

import pytest

from app.modelling.metrics import (
    accuracy,
    beats_naive_baseline,
    bias,
    brier_score,
    calibration_table,
    expected_calibration_error,
    favourite_calibration_table,
    log_loss,
    mae,
    rmse,
)


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


def test_beats_naive_baseline_true_for_clear_improvement():
    # 20% better than naive
    assert beats_naive_baseline(model_metric=0.20, naive_metric=0.25) is True


def test_beats_naive_baseline_false_for_marginal_improvement():
    # Poisson's actual holdout finding: ~0.2% better, nowhere near real skill
    assert beats_naive_baseline(model_metric=23.47, naive_metric=23.52) is False


def test_beats_naive_baseline_false_when_worse_than_naive():
    assert beats_naive_baseline(model_metric=0.30, naive_metric=0.25) is False


def test_beats_naive_baseline_respects_custom_threshold():
    # 6% improvement: passes a 5% bar, fails a 10% bar
    assert beats_naive_baseline(model_metric=0.235, naive_metric=0.25, min_improvement=0.05) is True
    assert beats_naive_baseline(model_metric=0.235, naive_metric=0.25, min_improvement=0.10) is False


def test_bias_zero_for_perfect_predictions():
    assert bias([100.0, 150.0], [100.0, 150.0]) == pytest.approx(0.0)


def test_bias_positive_means_overprediction():
    assert bias([110.0, 120.0], [100.0, 100.0]) == pytest.approx(15.0)


def test_bias_negative_means_underprediction():
    assert bias([90.0, 80.0], [100.0, 100.0]) == pytest.approx(-15.0)


def test_bias_cancels_out_symmetric_errors_unlike_mae():
    predictions = [110.0, 90.0]
    actuals = [100.0, 100.0]
    assert bias(predictions, actuals) == pytest.approx(0.0)
    assert mae(predictions, actuals) == pytest.approx(10.0)  # mae doesn't cancel


def test_bias_empty_is_nan():
    assert math.isnan(bias([], []))


def test_favourite_calibration_table_folds_home_and_away_favourites_together():
    # 0.85 (home favoured, wins) and 0.12 (away favoured at 88%, away wins,
    # i.e. home loses) should land in the same "85-90%" favourite bucket,
    # both counted as the favourite winning.
    predictions = [0.85, 0.12]
    outcomes = [1.0, 0.0]
    table = favourite_calibration_table(predictions, outcomes)
    bucket = next(r for r in table if r["bucket"] == "85%-90%")
    assert bucket["n"] == 2
    assert bucket["actual_rate"] == pytest.approx(1.0)  # the favourite won both times


def test_favourite_calibration_table_scores_favourite_losses_correctly():
    predictions = [0.9]  # home strongly favoured
    outcomes = [0.0]  # but away actually won — favourite lost
    table = favourite_calibration_table(predictions, outcomes)
    bucket = next(r for r in table if r["n"] > 0)
    assert bucket["actual_rate"] == pytest.approx(0.0)


def test_favourite_calibration_table_empty_buckets_have_none_rate():
    table = favourite_calibration_table([0.9], [1.0])
    empty_bucket = next(r for r in table if r["bucket"] == "50%-55%")
    assert empty_bucket["n"] == 0
    assert empty_bucket["actual_rate"] is None


def test_expected_calibration_error_zero_for_perfect_calibration():
    # every bucket's avg_predicted exactly matches its actual_rate
    rows = [
        {"bucket": "a", "n": 10, "avg_predicted": 0.6, "actual_rate": 0.6},
        {"bucket": "b", "n": 10, "avg_predicted": 0.8, "actual_rate": 0.8},
    ]
    assert expected_calibration_error(rows) == pytest.approx(0.0)


def test_expected_calibration_error_weights_by_bucket_size():
    rows = [
        {"bucket": "a", "n": 90, "avg_predicted": 0.6, "actual_rate": 0.6},  # perfectly calibrated, most of the data
        {"bucket": "b", "n": 10, "avg_predicted": 0.9, "actual_rate": 0.5},  # badly off, small bucket
    ]
    ece = expected_calibration_error(rows)
    # (90*0 + 10*0.4) / 100 = 0.04 — much closer to 0 than to the small bucket's raw 0.4 gap
    assert ece == pytest.approx(0.04)
    assert 0 < ece < 0.4


def test_expected_calibration_error_ignores_empty_buckets():
    rows = [
        {"bucket": "a", "n": 10, "avg_predicted": 0.6, "actual_rate": 0.6},
        {"bucket": "b", "n": 0, "avg_predicted": None, "actual_rate": None},
    ]
    assert expected_calibration_error(rows) == pytest.approx(0.0)


def test_expected_calibration_error_none_when_no_data():
    rows = [{"bucket": "a", "n": 0, "avg_predicted": None, "actual_rate": None}]
    assert expected_calibration_error(rows) is None
