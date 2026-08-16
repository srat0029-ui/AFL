"""Tests for live_staleness.py (Section 5's four-dimension freshness check)
and prop_math.py (Sections 12-15's implied-probability/fair-odds/EV/edge-
category math).
"""

from datetime import datetime, timezone

import pytest

from app.player_modelling.live_staleness import check_staleness
from app.player_modelling.prop_math import DEFAULT_EDGE_THRESHOLDS, categorize_edge, compare_model_to_market

OLD = datetime(2026, 1, 1, tzinfo=timezone.utc)
NEW = datetime(2026, 2, 1, tzinfo=timezone.utc)


def _fresh_kwargs(**overrides):
    base = dict(
        projection_model_version="v1", projection_data_cutoff=OLD, projection_lineup_status="expected_in",
        current_model_version="v1", current_data_cutoff=OLD, current_lineup_status="expected_in",
    )
    base.update(overrides)
    return base


# --- live_staleness.py ---


def test_check_staleness_fresh_projection_has_no_reasons():
    result = check_staleness(**_fresh_kwargs())
    assert result.is_stale is False
    assert result.reasons == []


def test_check_staleness_detects_model_retrain():
    result = check_staleness(**_fresh_kwargs(current_model_version="v2"))
    assert result.is_stale is True
    assert any("retrained" in r for r in result.reasons)


def test_check_staleness_detects_newer_data():
    result = check_staleness(**_fresh_kwargs(current_data_cutoff=NEW))
    assert result.is_stale is True
    assert any("recent match results" in r for r in result.reasons)


def test_check_staleness_detects_lineup_status_change():
    result = check_staleness(**_fresh_kwargs(current_lineup_status="uncertain"))
    assert result.is_stale is True
    assert any("Lineup status has changed" in r for r in result.reasons)


def test_check_staleness_detects_removed_lineup_record():
    result = check_staleness(**_fresh_kwargs(current_lineup_status=None))
    assert result.is_stale is True
    assert any("removed" in r for r in result.reasons)


def test_check_staleness_multiple_reasons_all_reported():
    result = check_staleness(**_fresh_kwargs(current_model_version="v2", current_lineup_status="expected_out"))
    assert len(result.reasons) == 2


# --- prop_math.py ---


def test_implied_probability_matches_known_example():
    comparison = compare_model_to_market(model_probability=0.6, offered_odds=1.90)
    assert comparison.raw_implied_probability == pytest.approx(1 / 1.90, abs=1e-9)
    assert comparison.raw_implied_probability == pytest.approx(0.5263, abs=1e-3)


def test_fair_odds_is_inverse_of_model_probability():
    comparison = compare_model_to_market(model_probability=0.5, offered_odds=2.0)
    assert comparison.model_fair_odds == pytest.approx(2.0, abs=1e-9)


def test_single_sided_market_does_not_claim_overround_removed():
    comparison = compare_model_to_market(model_probability=0.63, offered_odds=1.9)
    assert comparison.overround_removed is False
    assert comparison.devigged_probability is None
    assert comparison.difference_pp == pytest.approx(0.63 - (1 / 1.9), abs=1e-6)


def test_both_sides_quoted_devigs_and_removes_overround():
    comparison = compare_model_to_market(model_probability=0.63, offered_odds=1.9, opposite_side_odds=2.05)
    assert comparison.overround_removed is True
    assert comparison.devigged_probability is not None
    # devigged probabilities should sum to 1 with the implied opposite-side probability
    other_devigged = 1.0 - comparison.devigged_probability
    raw_this = 1 / 1.9
    raw_other = 1 / 2.05
    assert other_devigged == pytest.approx(raw_other / (raw_this + raw_other), abs=1e-6)


def test_expected_value_matches_known_example():
    # model thinks 60%, offered at $2.00 (implied 50%) -> positive EV
    comparison = compare_model_to_market(model_probability=0.6, offered_odds=2.0)
    expected_ev = 0.6 * (2.0 - 1.0) - 0.4 * 1.0
    assert comparison.expected_value == pytest.approx(expected_ev, abs=1e-9)


def test_categorize_edge_no_meaningful_difference_below_small_threshold():
    assert categorize_edge(0.01, "higher_confidence") == "no_meaningful_difference"


def test_categorize_edge_thresholds_with_higher_confidence():
    assert categorize_edge(0.04, "higher_confidence") == "small_difference"
    assert categorize_edge(0.07, "higher_confidence") == "moderate_difference"
    assert categorize_edge(0.12, "higher_confidence") == "larger_difference"


def test_categorize_edge_caps_at_small_for_lower_confidence():
    # A +12pp gap would be "larger_difference" at higher confidence, but is
    # capped to "small_difference" when confidence is lower - Section 15's
    # explicit requirement that the same numeric gap must not look the same
    # at different confidence levels.
    assert categorize_edge(0.12, "lower_confidence") == "small_difference"
    assert categorize_edge(0.12, "insufficient_history") == "small_difference"


def test_categorize_edge_caps_at_moderate_for_moderate_confidence():
    assert categorize_edge(0.12, "moderate_confidence") == "moderate_difference"


def test_categorize_edge_symmetric_for_negative_differences():
    assert categorize_edge(-0.12, "higher_confidence") == "larger_difference"


def test_default_edge_thresholds_are_configurable_not_hardcoded_in_function():
    from app.player_modelling.prop_math import EdgeCategoryThresholds

    custom = EdgeCategoryThresholds(small=0.10, moderate=0.20, larger=0.30)
    assert categorize_edge(0.12, "higher_confidence", thresholds=custom) == "small_difference"
    assert categorize_edge(0.12, "higher_confidence", thresholds=DEFAULT_EDGE_THRESHOLDS) == "larger_difference"
