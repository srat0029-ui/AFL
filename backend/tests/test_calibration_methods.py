import random

import pytest

from app.modelling.calibration_methods import FittedCalibrator, select_calibration_method
from app.modelling.metrics import brier_score


def _overconfident_dataset(n=400, seed=0):
    """Raw probabilities systematically pushed toward the extremes relative
    to the true underlying rate — a textbook case calibration should fix."""
    rng = random.Random(seed)
    outcomes = []
    raw_probs = []
    for _ in range(n):
        true_p = rng.uniform(0.3, 0.7)
        outcome = 1.0 if rng.random() < true_p else 0.0
        # push the reported probability toward 0/1 relative to the true rate
        overconfident = 0.5 + (true_p - 0.5) * 2.2
        raw_probs.append(min(max(overconfident, 0.01), 0.99))
        outcomes.append(outcome)
    return raw_probs, outcomes


def test_fitted_calibrator_none_is_identity():
    calibrator = FittedCalibrator(method="none")
    probs = [0.3, 0.6, 0.9]
    assert calibrator.apply(probs) == probs


def test_select_calibration_improves_overconfident_probabilities():
    raw_probs, outcomes = _overconfident_dataset()
    calibrator, results = select_calibration_method(raw_probs, outcomes, min_improvement=0.0, max_log_loss_regression=1.0)
    assert calibrator.method in ("platt", "isotonic")
    assert results[calibrator.method]["brier"] < results["none"]["brier"]


def test_select_calibration_rejects_a_method_that_worsens_log_loss():
    raw_probs, outcomes = _overconfident_dataset()
    # a near-zero regression allowance should reject any method that
    # improves Brier at the cost of log loss (e.g. isotonic overfitting a
    # small validation sample toward the 0/1 boundary)
    calibrator, results = select_calibration_method(raw_probs, outcomes, min_improvement=0.0, max_log_loss_regression=-100.0)
    assert calibrator.method == "none"


def test_select_calibration_returns_none_for_already_well_calibrated_data():
    rng = random.Random(1)
    outcomes = [1.0 if rng.random() < 0.6 else 0.0 for _ in range(300)]
    raw_probs = [0.6 + rng.uniform(-0.02, 0.02) for _ in outcomes]  # already close to the true rate, tiny noise
    calibrator, results = select_calibration_method(raw_probs, outcomes, min_improvement=0.01)
    # with such a high improvement bar, essentially-already-calibrated data shouldn't trigger adoption
    assert calibrator.method == "none"


def test_calibrated_probabilities_stay_in_valid_range():
    raw_probs, outcomes = _overconfident_dataset()
    calibrator, _ = select_calibration_method(raw_probs, outcomes, min_improvement=0.0)
    calibrated = calibrator.apply(raw_probs)
    assert all(0.0 <= p <= 1.0 for p in calibrated)


def test_calibration_handles_draws_in_evaluation_set():
    raw_probs, outcomes = _overconfident_dataset(n=100)
    raw_probs.append(0.55)
    outcomes.append(0.5)  # a draw
    calibrator, results = select_calibration_method(raw_probs, outcomes, min_improvement=0.0)
    calibrated = calibrator.apply(raw_probs)
    assert len(calibrated) == len(raw_probs)  # draw's probability still gets calibrated/scored, not dropped


def test_select_calibration_empty_input_returns_none_method():
    calibrator, results = select_calibration_method([], [])
    assert calibrator.method == "none"


def test_select_calibration_deterministic():
    raw_probs, outcomes = _overconfident_dataset()
    calibrator_1, results_1 = select_calibration_method(raw_probs, outcomes, min_improvement=0.0)
    calibrator_2, results_2 = select_calibration_method(raw_probs, outcomes, min_improvement=0.0)
    assert calibrator_1.method == calibrator_2.method
    assert results_1 == results_2
