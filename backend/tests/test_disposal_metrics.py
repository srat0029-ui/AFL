"""Tests for disposal_metrics.py's count-specific metrics and
disposal_confidence.py's tiering rules — Section 26's "prediction interval
calculations" and confidence-tier coverage.
"""

import pytest

from app.player_modelling.disposal_confidence import ConfidenceInputs, ConfidenceTier, classify_confidence
from app.player_modelling.disposal_metrics import interval_coverage, mean_interval_width, median_absolute_error, within_k_accuracy


def test_median_absolute_error_basic():
    assert median_absolute_error([10, 20, 30], [10, 15, 40]) == pytest.approx(5.0)


def test_within_k_accuracy_all_within():
    assert within_k_accuracy([10, 20], [11, 19], k=2) == 1.0


def test_within_k_accuracy_partial():
    # errors: |10-15|=5, |20-21|=1 -> only the second is within k=2
    assert within_k_accuracy([10, 20], [15, 21], k=2) == 0.5


def test_interval_coverage_counts_actuals_inside_bounds():
    lowers = [10, 10, 10]
    uppers = [20, 20, 20]
    actuals = [15, 25, 5]  # inside, above, below
    assert interval_coverage(lowers, uppers, actuals) == pytest.approx(1 / 3)


def test_interval_coverage_full_when_all_inside():
    assert interval_coverage([0, 0], [100, 100], [50, 60]) == 1.0


def test_mean_interval_width():
    assert mean_interval_width([10, 20], [15, 30]) == pytest.approx(7.5)


# --- Confidence tiers ---


def test_insufficient_history_below_min_games():
    tier = classify_confidence(
        ConfidenceInputs(games_of_history=2, tog_last5_avg=80, disposals_last5_std=3, league_low_tog_cutoff=60)
    )
    assert tier == ConfidenceTier.INSUFFICIENT_HISTORY


def test_lower_confidence_for_unstable_recent_tog():
    tier = classify_confidence(
        ConfidenceInputs(games_of_history=50, tog_last5_avg=40, disposals_last5_std=3, league_low_tog_cutoff=60)
    )
    assert tier == ConfidenceTier.LOWER


def test_lower_confidence_for_high_disposal_variance():
    tier = classify_confidence(
        ConfidenceInputs(games_of_history=50, tog_last5_avg=80, disposals_last5_std=9, league_low_tog_cutoff=60)
    )
    assert tier == ConfidenceTier.LOWER


def test_higher_confidence_for_established_stable_player():
    tier = classify_confidence(
        ConfidenceInputs(games_of_history=100, tog_last5_avg=85, disposals_last5_std=2, league_low_tog_cutoff=60)
    )
    assert tier == ConfidenceTier.HIGHER


def test_moderate_confidence_for_mid_history_stable_player():
    tier = classify_confidence(
        ConfidenceInputs(games_of_history=15, tog_last5_avg=85, disposals_last5_std=2, league_low_tog_cutoff=60)
    )
    assert tier == ConfidenceTier.MODERATE
