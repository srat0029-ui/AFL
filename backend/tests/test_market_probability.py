import numpy as np
import pytest

from app.edges.market_probability import h2h_probability, line_probability, total_probability


def _spike_pmf(score: int, length: int = 200) -> np.ndarray:
    pmf = np.zeros(length)
    pmf[score] = 1.0
    return pmf


# home scores 100 (deterministic), away scores 80 (deterministic) -> margin = +20 exactly
HOME_PMF = _spike_pmf(100)
AWAY_PMF = _spike_pmf(80)


def test_h2h_probability_home_selection_returns_elo_prob_directly():
    assert h2h_probability(0.64, "Collingwood", "Collingwood", "Essendon") == pytest.approx(0.64)


def test_h2h_probability_away_selection_is_complement():
    assert h2h_probability(0.64, "Essendon", "Collingwood", "Essendon") == pytest.approx(0.36)


def test_h2h_probability_unknown_selection_raises():
    with pytest.raises(ValueError):
        h2h_probability(0.64, "Richmond", "Collingwood", "Essendon")


def test_line_probability_home_favourite_covers_a_smaller_line():
    # home won by 20; "-12.5" (must win by >12.5) is covered
    prob = line_probability(HOME_PMF, AWAY_PMF, "Home", -12.5, "Home", "Away")
    assert prob == pytest.approx(1.0)


def test_line_probability_home_favourite_fails_to_cover_a_bigger_line():
    # home won by 20; "-25" (must win by >25) is NOT covered
    prob = line_probability(HOME_PMF, AWAY_PMF, "Home", -25.0, "Home", "Away")
    assert prob == pytest.approx(0.0)


def test_line_probability_away_underdog_fails_to_cover_a_smaller_line():
    # home won by 20; away "+12.5" needed margin < 12.5, but actual margin is 20 -> NOT covered
    prob = line_probability(HOME_PMF, AWAY_PMF, "Away", 12.5, "Home", "Away")
    assert prob == pytest.approx(0.0)


def test_line_probability_away_underdog_covers_a_bigger_line():
    # away "+25" needed margin < 25; actual margin is 20 -> covered
    prob = line_probability(HOME_PMF, AWAY_PMF, "Away", 25.0, "Home", "Away")
    assert prob == pytest.approx(1.0)


def test_line_probability_home_and_away_are_complementary_at_mirrored_lines():
    # standard bookmaker pairing: Home -12.5 / Away +12.5 should sum to 1
    home_prob = line_probability(HOME_PMF, AWAY_PMF, "Home", -12.5, "Home", "Away")
    away_prob = line_probability(HOME_PMF, AWAY_PMF, "Away", 12.5, "Home", "Away")
    assert home_prob + away_prob == pytest.approx(1.0)


def test_line_probability_unknown_selection_raises():
    with pytest.raises(ValueError):
        line_probability(HOME_PMF, AWAY_PMF, "Richmond", -12.5, "Home", "Away")


def test_total_probability_over_covered_when_actual_total_exceeds_line():
    # total = 180; over 175 is covered
    assert total_probability(HOME_PMF, AWAY_PMF, "over", 175.0) == pytest.approx(1.0)


def test_total_probability_over_not_covered_when_line_exceeds_actual_total():
    assert total_probability(HOME_PMF, AWAY_PMF, "over", 185.0) == pytest.approx(0.0)


def test_total_probability_under_is_complement_of_over():
    over = total_probability(HOME_PMF, AWAY_PMF, "over", 175.0)
    under = total_probability(HOME_PMF, AWAY_PMF, "under", 175.0)
    assert over + under == pytest.approx(1.0)


def test_total_probability_case_insensitive_selection():
    assert total_probability(HOME_PMF, AWAY_PMF, "Over", 175.0) == pytest.approx(1.0)
    assert total_probability(HOME_PMF, AWAY_PMF, "UNDER", 175.0) == pytest.approx(0.0)


def test_total_probability_invalid_selection_raises():
    with pytest.raises(ValueError):
        total_probability(HOME_PMF, AWAY_PMF, "Home", 175.0)
