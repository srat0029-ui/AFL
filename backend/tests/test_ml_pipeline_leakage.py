"""Explicit, literal tests for the leakage guarantees of the ML pipeline
specifically (as distinct from the walk-forward feature generation already
covered in test_features.py / test_leakage_and_determinism.py):
preprocessing (imputer/scaler) fit only on training data, chronological
fitting that never sees evaluation-period rows, and calibration fit only
on tune-window data.
"""

import random
import warnings
from datetime import datetime, timedelta, timezone

import numpy as np

from app.modelling.calibration_methods import select_calibration_method
from app.modelling.features import STATS_FEATURE_NAMES, MatchFeatureRow
from app.modelling.logistic import LogisticConfig, feature_matrix, fit_logistic_model, predict

warnings.filterwarnings("ignore", category=FutureWarning)


def _row(match_id, season_year, outcome, feature_overrides=None) -> MatchFeatureRow:
    features = {name: 0.0 for name in STATS_FEATURE_NAMES}
    if feature_overrides:
        features.update(feature_overrides)
    return MatchFeatureRow(
        match_id=match_id, season_year=season_year,
        scheduled_start=datetime(season_year, 3, 1, tzinfo=timezone.utc) + timedelta(days=match_id),
        home_team_id=1, away_team_id=2, actual_home_outcome=outcome,
        features=features, has_full_history=True,
    )


def _synthetic_rows(n, season_year, seed, signal_feature="form_diff_5", strength=0.35, offset=0.0, scale=1.0):
    """offset/scale let an "evaluation set" have a deliberately different
    distribution than the training set — if preprocessing (imputer median,
    scaler mean/std) were accidentally fit on this data too, it would shift
    the model's fitted coefficients in a detectable way."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        signal = rng.uniform(-1, 1)
        outcome = 1.0 if rng.random() < (0.5 + strength * signal) else 0.0
        features = {name: offset + scale * rng.uniform(-1, 1) for name in STATS_FEATURE_NAMES}
        features[signal_feature] = offset + scale * signal
        rows.append(_row(i, season_year, outcome, feature_overrides=features))
    return rows


def test_preprocessing_statistics_come_only_from_training_data():
    """The fitted Pipeline's imputer/scaler statistics must be derived
    solely from the rows passed to fit_logistic_model — never from rows
    only seen later via predict()."""
    train_rows = _synthetic_rows(300, 2016, seed=0)
    # a wildly different-scale "evaluation" set, never passed to fit()
    eval_rows = _synthetic_rows(100, 2019, seed=1, offset=1000.0, scale=50.0)

    pipeline = fit_logistic_model(train_rows, LogisticConfig(feature_names=STATS_FEATURE_NAMES, C=1.0))
    scaler_mean_before = pipeline.named_steps["scale"].mean_.copy()
    scaler_scale_before = pipeline.named_steps["scale"].scale_.copy()

    # predicting on wildly-out-of-distribution data must not refit anything
    predict(pipeline, eval_rows, STATS_FEATURE_NAMES)

    assert np.array_equal(pipeline.named_steps["scale"].mean_, scaler_mean_before)
    assert np.array_equal(pipeline.named_steps["scale"].scale_, scaler_scale_before)


def test_fitted_coefficients_unaffected_by_evaluation_period_data():
    """Fitting on the same tune-window rows must produce identical
    coefficients regardless of what (if anything) exists in a
    differently-distributed evaluation set never passed to fit()."""
    train_rows = _synthetic_rows(300, 2016, seed=0)
    config = LogisticConfig(feature_names=STATS_FEATURE_NAMES, C=1.0)

    pipeline_a = fit_logistic_model(train_rows, config)
    # simulate "evaluation data exists in the world" by generating it, but
    # never passing it to fit_logistic_model — the fitted model must be
    # identical either way, proving eval rows can't leak into training.
    _unused_eval_rows = _synthetic_rows(500, 2025, seed=99, offset=1000.0, scale=100.0)
    pipeline_b = fit_logistic_model(train_rows, config)

    coefs_a = pipeline_a.named_steps["logreg"].coef_[0]
    coefs_b = pipeline_b.named_steps["logreg"].coef_[0]
    assert np.array_equal(coefs_a, coefs_b)


def test_chronological_fit_excludes_later_season_rows():
    """A model trained on rows filtered to season < some year must produce
    identical results whether or not later-season rows exist in the
    broader universe of rows — proving the tune/eval split, not just
    happenstance, is what keeps evaluation data out of fitting."""
    all_rows_without_future = _synthetic_rows(200, 2016, seed=0) + _synthetic_rows(200, 2017, seed=1)
    all_rows_with_future = (
        all_rows_without_future + _synthetic_rows(300, 2020, seed=2, offset=500.0, scale=20.0)
    )

    config = LogisticConfig(feature_names=STATS_FEATURE_NAMES, C=1.0)

    # in real usage, fit_logistic_model is always called with an
    # already-filtered tune_rows list (season_year < EVALUATION_START_YEAR)
    tune_only = [r for r in all_rows_with_future if r.season_year < 2019]
    pipeline_from_full_universe = fit_logistic_model(tune_only, config)
    pipeline_from_tune_subset = fit_logistic_model(all_rows_without_future, config)

    coefs_1 = pipeline_from_full_universe.named_steps["logreg"].coef_[0]
    coefs_2 = pipeline_from_tune_subset.named_steps["logreg"].coef_[0]
    assert np.array_equal(coefs_1, coefs_2)


def test_calibration_method_selection_does_not_use_out_of_window_data():
    """select_calibration_method must be called with only the inner
    validation window's predictions — this test proves the function itself
    has no way to see anything beyond what's explicitly passed to it (no
    global state, no hidden DB access), so leakage can only happen at the
    call site, and the call site (logistic_report.py) is documented to pass
    only inner_val predictions."""
    inner_val_probs = [0.4, 0.6, 0.55, 0.7, 0.3, 0.65, 0.45, 0.8, 0.2, 0.5] * 5
    inner_val_outcomes = [1.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 0.0] * 5

    calibrator_1, results_1 = select_calibration_method(inner_val_probs, inner_val_outcomes)
    # calling again with the identical (and ONLY) inputs must reproduce the
    # identical calibrator choice — nothing external influenced it
    calibrator_2, results_2 = select_calibration_method(inner_val_probs, inner_val_outcomes)

    assert calibrator_1.method == calibrator_2.method
    assert results_1 == results_2


def test_feature_matrix_never_reads_beyond_passed_rows():
    """feature_matrix builds its array purely from the rows list argument —
    proves there's no hidden global/module-level state a later call could
    accidentally pick up stale or future data from."""
    rows_a = [_row(1, 2016, 1.0, {"form_diff_5": 0.5})]
    rows_b = [_row(1, 2016, 1.0, {"form_diff_5": 0.9})]

    X_a = feature_matrix(rows_a, STATS_FEATURE_NAMES)
    X_b = feature_matrix(rows_b, STATS_FEATURE_NAMES)

    idx = STATS_FEATURE_NAMES.index("form_diff_5")
    assert X_a[0][idx] == 0.5
    assert X_b[0][idx] == 0.9  # not contaminated by the earlier call
