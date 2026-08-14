import pytest

from app.edges.fair_odds import expected_value, fair_odds_from_probability


def test_fair_odds_known_values():
    assert fair_odds_from_probability(0.5) == pytest.approx(2.0)
    assert fair_odds_from_probability(0.25) == pytest.approx(4.0)


def test_fair_odds_rejects_invalid_probability():
    with pytest.raises(ValueError):
        fair_odds_from_probability(0.0)
    with pytest.raises(ValueError):
        fair_odds_from_probability(1.5)


def test_expected_value_zero_at_fair_price():
    # model says 50%, price is exactly fair odds (2.0) -> EV = 0
    assert expected_value(0.5, 2.0) == pytest.approx(0.0)


def test_expected_value_positive_when_price_better_than_fair():
    # model says 50% (fair = 2.0), price offered is 2.20 -> positive EV
    ev = expected_value(0.5, 2.20)
    assert ev > 0
    assert ev == pytest.approx(0.5 * 1.20 - 0.5 * 1.0)


def test_expected_value_negative_when_price_worse_than_fair():
    ev = expected_value(0.5, 1.80)
    assert ev < 0


def test_expected_value_matches_users_example():
    # from the original brief: Collingwood win prob 64%, market odds $1.80
    ev = expected_value(0.64, 1.80)
    assert ev == pytest.approx(0.64 * 0.80 - 0.36 * 1.0)
    assert ev > 0  # matches the brief's framing of this as a positive-edge example
