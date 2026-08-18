"""Tests for projection-vs-line distance (Weekly Bet Review stage, Section 6)."""

from app.player_modelling.projection_line_distance import (
    player_threshold_distance,
    team_line_distance,
    team_total_distance,
)


def test_team_line_distance_positive_when_favourite_clears_handicap():
    d = team_line_distance(expected_margin_for_selection=21.6, line_value=-11.5)
    assert d.distance == 21.6 + (-11.5)
    assert d.distance > 0


def test_team_line_distance_negative_when_underdog_projected_to_lose_by_more_than_line():
    d = team_line_distance(expected_margin_for_selection=-30.0, line_value=24.5)
    assert d.distance == -30.0 + 24.5
    assert d.distance < 0


def test_team_line_distance_real_west_coast_case():
    # Real case from this stage's verification: West Coast +44.5, model
    # projects losing by 37.9, so they cover by 6.6.
    d = team_line_distance(expected_margin_for_selection=-37.94, line_value=44.5)
    assert round(d.distance, 1) == 6.6


def test_team_total_distance_over_positive_when_model_exceeds_line():
    d = team_total_distance(expected_total_points=190.0, line_value=180.5, selection="over")
    assert d.distance == 190.0 - 180.5


def test_team_total_distance_under_positive_when_model_below_line():
    d = team_total_distance(expected_total_points=170.0, line_value=180.5, selection="under")
    assert d.distance == 180.5 - 170.0
    assert d.distance > 0


def test_team_total_distance_under_negative_when_model_exceeds_line():
    d = team_total_distance(expected_total_points=190.0, line_value=180.5, selection="under")
    assert d.distance < 0


def test_player_threshold_distance_matches_worked_example():
    # Section 6's own example: projected 27.8, line 24.5, distance +3.3.
    d = player_threshold_distance(predicted_mean=27.8, threshold=24.5, market_type="player_disposals")
    assert round(d.distance, 1) == 3.3
    assert d.unit == "disposals"


def test_player_threshold_distance_goals_unit():
    d = player_threshold_distance(predicted_mean=1.8, threshold=2.5, market_type="player_goals")
    assert d.unit == "goals"
    assert d.distance < 0
