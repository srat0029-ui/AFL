"""Remaining Section 26 coverage: model fitting never sees eval data, and
disposal_evaluation.py's calibration-table integration is wired correctly.
"""

import numpy as np

from app.player_modelling.disposal_backtest import PredictionRecord
from app.player_modelling.disposal_evaluation import compute_threshold_metrics
from app.player_modelling.disposal_features import DisposalFeatureRow
from app.player_modelling.disposal_models import feature_matrix, fit_ridge, target_vector

FEATURE_NAMES = ("disposals_last5_avg", "tog_last5_avg")


def _feature_row(match_id, disposals, last5_avg):
    return DisposalFeatureRow(
        player_id=1,
        match_id=match_id,
        team_id=1,
        opponent_team_id=2,
        season_year=2020,
        round_number=1,
        is_final=False,
        scheduled_start=None,
        disposals=disposals,
        games_of_history=5,
        features={"disposals_last5_avg": last5_avg, "tog_last5_avg": 80.0},
    )


def test_fitting_a_model_does_not_require_or_touch_eval_rows():
    """A model must be fittable from tune_rows alone - if fit_ridge secretly
    needed eval data it would raise/fail here, since none is passed."""
    train_rows = [_feature_row(i, disposals=10 + i, last5_avg=10 + i) for i in range(50)]
    model = fit_ridge(train_rows, FEATURE_NAMES)

    # predicting on rows the model has never seen (a stand-in for "eval rows") works fine
    unseen_rows = [_feature_row(1000 + i, disposals=999, last5_avg=30 + i) for i in range(5)]
    X_unseen = feature_matrix(unseen_rows, FEATURE_NAMES)
    predictions = model.predict_fn(X_unseen)
    assert len(predictions) == 5
    assert all(p >= 0 for p in predictions)  # clipped non-negative


def test_model_predictions_do_not_depend_on_row_order_of_training_set():
    """A real leakage smell would be a model whose fit result depends on
    which rows happen to be adjacent to which - shuffling the training set
    should not change the fitted coefficients (Ridge is order-independent
    by construction; this pins that down as a regression test)."""
    rows = [_feature_row(i, disposals=10 + (i % 7), last5_avg=10 + (i % 5)) for i in range(60)]
    shuffled = list(reversed(rows))

    model_a = fit_ridge(rows, FEATURE_NAMES)
    model_b = fit_ridge(shuffled, FEATURE_NAMES)

    test_rows = [_feature_row(500, disposals=0, last5_avg=18.0)]
    X = feature_matrix(test_rows, FEATURE_NAMES)
    assert model_a.predict_fn(X)[0] == model_b.predict_fn(X)[0]


def test_target_vector_matches_actual_disposals_not_features():
    rows = [_feature_row(1, disposals=25, last5_avg=10), _feature_row(2, disposals=5, last5_avg=20)]
    y = target_vector(rows)
    assert list(y) == [25.0, 5.0]


def test_compute_threshold_metrics_calibration_integration():
    """Confirms disposal_evaluation.py's wrapper around
    app/modelling/metrics.py's calibration_table produces a sane,
    correctly-shaped result for a real PredictionRecord list."""
    rng = np.random.default_rng(0)
    residuals = np.sort(rng.normal(0, 5, 200))
    predictions = [
        PredictionRecord(
            player_id=1, match_id=i, team_id=1, season_year=2020, is_final=False,
            games_of_history=20, tog_last5_avg=80.0, disposals_last5_std=3.0,
            actual=int(rng.normal(18, 5)), predicted_mean=18.0, nb_alpha=0.05,
            empirical_residuals=residuals,
        )
        for i in range(200)
    ]
    result = compute_threshold_metrics(predictions, threshold=20, distribution="nb")
    assert result.n == 200
    assert 0.0 <= result.brier <= 1.0
    assert result.ece is None or result.ece >= 0.0
    assert sum(row["n"] for row in result.calibration) == 200  # every prediction lands in exactly one bucket


def test_promoted_feature_names_exactly_match_player_history_plus_opponent_ablation():
    """Regression test for a real bug: PROMOTED_DISPOSAL_FEATURE_NAMES was
    first hand-written to mirror disposal_analysis.FEATURE_GROUPS'
    "player_history" + "opponent_context" combination (the configuration
    the report's ambiguity resolution actually validated) but silently
    dropped one feature (opponent_expected_score). Pins the two to be
    identical going forward so they can't drift apart again."""
    from app.player_modelling.disposal_analysis import FEATURE_GROUPS
    from app.player_modelling.disposal_features import PROMOTED_DISPOSAL_FEATURE_NAMES

    expected = set(FEATURE_GROUPS["player_history"]) | set(FEATURE_GROUPS["opponent_context"])
    assert set(PROMOTED_DISPOSAL_FEATURE_NAMES) == expected
    assert len(PROMOTED_DISPOSAL_FEATURE_NAMES) == len(set(PROMOTED_DISPOSAL_FEATURE_NAMES))  # no duplicates
