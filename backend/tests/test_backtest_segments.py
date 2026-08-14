from dataclasses import dataclass

import pytest

from app.backtesting.segments import (
    build_segments,
    conviction_bucket,
    scoring_metrics,
    team_perspective,
    win_prob_metrics,
    win_prob_metrics_from_pairs,
)


@dataclass(frozen=True)
class FakePrediction:
    home_team_id: int
    away_team_id: int
    season_year: int
    home_win_probability: float
    actual_home_outcome: float
    expected_total_points: float = 0.0
    actual_total_points: float = 0.0
    expected_margin: float = 0.0
    actual_margin: float = 0.0


def test_win_prob_metrics_empty_list():
    assert win_prob_metrics([]) == {}


def test_win_prob_metrics_perfect_predictions():
    preds = [
        FakePrediction(1, 2, 2024, 1.0, 1.0),
        FakePrediction(1, 2, 2024, 0.0, 0.0),
    ]
    metrics = win_prob_metrics(preds)
    assert metrics["brier_score"] == pytest.approx(0.0)
    assert metrics["accuracy"] == pytest.approx(1.0)


def test_win_prob_metrics_from_pairs_empty():
    assert win_prob_metrics_from_pairs([], []) == {}


def test_win_prob_metrics_from_pairs_matches_direct_call():
    preds = [FakePrediction(1, 2, 2024, 0.7, 1.0), FakePrediction(1, 2, 2024, 0.3, 0.0)]
    from_predictions = win_prob_metrics(preds)
    from_pairs = win_prob_metrics_from_pairs([0.7, 0.3], [1.0, 0.0])
    assert from_predictions == from_pairs


def test_scoring_metrics_empty_list():
    assert scoring_metrics([]) == {}


def test_scoring_metrics_known_mae():
    preds = [
        FakePrediction(1, 2, 2024, 0.5, 0.5, expected_total_points=160, actual_total_points=150, expected_margin=10, actual_margin=0),
    ]
    metrics = scoring_metrics(preds)
    assert metrics["total_points_mae"] == pytest.approx(10.0)
    assert metrics["margin_mae"] == pytest.approx(10.0)


def test_build_segments_groups_and_sorts():
    preds = [
        FakePrediction(1, 2, 2024, 0.6, 1.0),
        FakePrediction(1, 2, 2023, 0.6, 1.0),
        FakePrediction(1, 2, 2024, 0.4, 0.0),
    ]
    segments = build_segments(preds, key_fn=lambda p: str(p.season_year), metrics_fn=win_prob_metrics)

    assert [s.label for s in segments] == ["2023", "2024"]
    assert segments[0].n == 1
    assert segments[1].n == 2


def test_build_segments_empty_input():
    assert build_segments([], key_fn=lambda p: "x", metrics_fn=win_prob_metrics) == []


class TestConvictionBucket:
    def test_near_even_bucket(self):
        assert conviction_bucket(0.52) == "50-60%"
        assert conviction_bucket(0.48) == "50-60%"  # symmetric: away favourite at 52%

    def test_moderate_bucket(self):
        assert conviction_bucket(0.65) == "60-70%"

    def test_high_bucket(self):
        assert conviction_bucket(0.80) == "70-85%"

    def test_very_high_bucket(self):
        assert conviction_bucket(0.95) == "85-100%"
        assert conviction_bucket(0.02) == "85-100%"  # strong away favourite

    def test_exact_boundary_goes_to_higher_bucket(self):
        assert conviction_bucket(0.70) == "70-85%"

    def test_certain_prediction(self):
        assert conviction_bucket(1.0) == "85-100%"


class TestTeamPerspective:
    def test_home_team_returns_direct_values(self):
        pred = FakePrediction(home_team_id=1, away_team_id=2, season_year=2024, home_win_probability=0.7, actual_home_outcome=1.0)
        assert team_perspective(pred, team_id=1) == (0.7, 1.0)

    def test_away_team_returns_complemented_values(self):
        pred = FakePrediction(home_team_id=1, away_team_id=2, season_year=2024, home_win_probability=0.7, actual_home_outcome=1.0)
        prob, outcome = team_perspective(pred, team_id=2)
        assert prob == pytest.approx(0.3)
        assert outcome == pytest.approx(0.0)

    def test_uninvolved_team_returns_none(self):
        pred = FakePrediction(home_team_id=1, away_team_id=2, season_year=2024, home_win_probability=0.7, actual_home_outcome=1.0)
        assert team_perspective(pred, team_id=999) is None

    def test_draw_symmetric_for_both_teams(self):
        pred = FakePrediction(home_team_id=1, away_team_id=2, season_year=2024, home_win_probability=0.5, actual_home_outcome=0.5)
        assert team_perspective(pred, team_id=1) == (0.5, 0.5)
        assert team_perspective(pred, team_id=2) == (0.5, 0.5)
