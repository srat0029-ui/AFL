"""Tests for similar-situation calibration bands (Weekly Bet Review
stage, Section 4) — model-level probability buckets, never a specific
team's/player's own history."""

from app.player_modelling.similar_situation_calibration import _band_containing, _bucket_predictions


def test_bucket_predictions_5pt_bands_match_worked_example():
    # 0.462 must land in the 45%-50% band specifically, not a 40-50% band -
    # this is the exact precision bug this module was built to avoid.
    probs = [0.46, 0.47, 0.48, 0.30, 0.90]
    outcomes = [1.0, 0.0, 1.0, 0.0, 1.0]
    rows = _bucket_predictions(probs, outcomes, n_bins=20)
    band = _band_containing(rows, 0.462)
    assert round(band["lo"], 2) == 0.45
    assert round(band["hi"], 2) == 0.50
    assert band["n"] == 3


def test_bucket_predictions_10pt_bands_match_worked_example():
    # Section 4's disposal example: 60-70% band.
    probs = [0.61, 0.65, 0.69, 0.20]
    outcomes = [1.0, 1.0, 0.0, 0.0]
    rows = _bucket_predictions(probs, outcomes, n_bins=10)
    band = _band_containing(rows, 0.65)
    assert round(band["lo"], 1) == 0.6
    assert round(band["hi"], 1) == 0.7
    assert band["n"] == 3


def test_band_actual_rate_reflects_observed_outcomes():
    probs = [0.65, 0.65]
    outcomes = [1.0, 0.0]
    rows = _bucket_predictions(probs, outcomes, n_bins=10)
    band = _band_containing(rows, 0.65)
    assert band["actual_rate"] == 0.5


def test_band_containing_handles_probability_at_top_edge():
    probs = [0.99]
    outcomes = [1.0]
    rows = _bucket_predictions(probs, outcomes, n_bins=10)
    band = _band_containing(rows, 1.0)
    assert band is not None


def test_empty_bucket_has_zero_n_and_none_rates():
    rows = _bucket_predictions([0.05], [1.0], n_bins=10)
    empty_band = _band_containing(rows, 0.85)
    assert empty_band["n"] == 0
    assert empty_band["avg_predicted"] is None
    assert empty_band["actual_rate"] is None
