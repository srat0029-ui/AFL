"""Tests for the player-modelling architecture scaffold (app/player_modelling/).
No real projection model exists yet — these tests exercise the type shapes
and dispatch logic (MarketLine formatting, PlayerProjection routing a query
to prob_at_least vs prob_over) using a minimal fake distribution, not any
real statistical model.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from app.player_modelling.features import PlayerFeatureInput, PlayerFeatureRow
from app.player_modelling.market import COMMON_DISPOSAL_LINES, COMMON_GOAL_LINES, LineType, MarketLine, PlayerMarket
from app.player_modelling.projection import PlayerProjection, ProjectionDistribution


@dataclass(frozen=True)
class _FakeDistribution:
    """A minimal stand-in satisfying the ProjectionDistribution protocol,
    with hand-picked values so dispatch can be asserted precisely."""

    _mean: float

    def mean(self) -> float:
        return self._mean

    def prob_at_least(self, threshold: float) -> float:
        return 0.42 if threshold <= self._mean else 0.10

    def prob_over(self, line: float) -> float:
        return 0.55 if line <= self._mean else 0.05


def test_fake_distribution_satisfies_the_protocol():
    assert isinstance(_FakeDistribution(29.8), ProjectionDistribution)


def test_market_line_multi_plus_label():
    line = MarketLine(PlayerMarket.DISPOSALS, LineType.MULTI_PLUS, 30)
    assert line.label() == "30+"


def test_market_line_over_under_label():
    line = MarketLine(PlayerMarket.DISPOSALS, LineType.OVER_UNDER, 27.5)
    assert line.label() == "over/under 27.5"


def test_common_disposal_lines_cover_expected_thresholds():
    multi_plus = {l.threshold for l in COMMON_DISPOSAL_LINES if l.line_type is LineType.MULTI_PLUS}
    assert multi_plus == {20, 25, 30, 35}
    assert any(l.line_type is LineType.OVER_UNDER and l.threshold == 27.5 for l in COMMON_DISPOSAL_LINES)
    assert all(l.market is PlayerMarket.DISPOSALS for l in COMMON_DISPOSAL_LINES)


def test_common_goal_lines_cover_expected_thresholds():
    thresholds = {l.threshold for l in COMMON_GOAL_LINES}
    assert thresholds == {1, 2, 3, 4}
    assert all(l.line_type is LineType.MULTI_PLUS for l in COMMON_GOAL_LINES)
    assert all(l.market is PlayerMarket.GOALS for l in COMMON_GOAL_LINES)


def test_projection_probability_for_line_dispatches_multi_plus_to_prob_at_least():
    projection = PlayerProjection(
        player_id=1, match_id=1, market=PlayerMarket.DISPOSALS,
        distribution=_FakeDistribution(29.8), model_name="test", games_of_history=10,
    )
    line = MarketLine(PlayerMarket.DISPOSALS, LineType.MULTI_PLUS, 25)
    assert projection.probability_for_line(line) == 0.42


def test_projection_probability_for_line_dispatches_over_under_to_prob_over():
    projection = PlayerProjection(
        player_id=1, match_id=1, market=PlayerMarket.DISPOSALS,
        distribution=_FakeDistribution(29.8), model_name="test", games_of_history=10,
    )
    line = MarketLine(PlayerMarket.DISPOSALS, LineType.OVER_UNDER, 27.5)
    assert projection.probability_for_line(line) == 0.55


def test_projection_rejects_a_line_for_a_different_market():
    projection = PlayerProjection(
        player_id=1, match_id=1, market=PlayerMarket.DISPOSALS,
        distribution=_FakeDistribution(29.8), model_name="test", games_of_history=10,
    )
    goal_line = MarketLine(PlayerMarket.GOALS, LineType.MULTI_PLUS, 2)
    with pytest.raises(ValueError):
        projection.probability_for_line(goal_line)


def test_player_feature_input_and_row_construct_with_expected_shape():
    feature_input = PlayerFeatureInput(
        player_id=1, match_id=1, team_id=1, opponent_team_id=2,
        season_year=2024, round_number=5, scheduled_start=datetime(2024, 4, 1, tzinfo=timezone.utc),
    )
    assert feature_input.round_number == 5

    row = PlayerFeatureRow(
        player_id=1, match_id=1, market=PlayerMarket.DISPOSALS,
        features={"disposals_avg_5": 27.4, "disposals_avg_10": None},
        has_sufficient_history=True,
    )
    assert row.features["disposals_avg_5"] == 27.4
    assert row.features["disposals_avg_10"] is None
    assert row.has_sufficient_history is True


def test_player_feature_row_defaults_to_insufficient_history():
    row = PlayerFeatureRow(player_id=1, match_id=1, market=PlayerMarket.GOALS)
    assert row.has_sufficient_history is False
    assert row.features == {}
