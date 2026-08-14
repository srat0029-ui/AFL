import pytest

from app.edges.overround import implied_probability, overround, remove_overround


def test_implied_probability_known_value():
    assert implied_probability(2.0) == pytest.approx(0.5)
    assert implied_probability(4.0) == pytest.approx(0.25)


def test_implied_probability_rejects_invalid_price():
    with pytest.raises(ValueError):
        implied_probability(1.0)
    with pytest.raises(ValueError):
        implied_probability(0.5)


def test_overround_zero_for_fair_book():
    # 1/2.0 + 1/2.0 = 1.0 exactly -> no margin
    assert overround({"A": 2.0, "B": 2.0}) == pytest.approx(0.0)


def test_overround_positive_for_realistic_book():
    # a typical AFL h2h book: e.g. $1.90 / $1.90 -> overround > 0
    margin = overround({"A": 1.90, "B": 1.90})
    assert margin > 0
    assert margin == pytest.approx((1 / 1.90 + 1 / 1.90) - 1.0)


def test_remove_overround_sums_to_one():
    fair = remove_overround({"A": 1.85, "B": 2.05})
    assert sum(fair.values()) == pytest.approx(1.0)


def test_remove_overround_preserves_relative_ordering():
    fair = remove_overround({"favourite": 1.50, "underdog": 3.00})
    assert fair["favourite"] > fair["underdog"]


def test_remove_overround_symmetric_book_gives_fifty_fifty():
    fair = remove_overround({"A": 1.90, "B": 1.90})
    assert fair["A"] == pytest.approx(0.5)
    assert fair["B"] == pytest.approx(0.5)


def test_remove_overround_matches_hand_calculation():
    # implied: 1/1.80=0.5556, 1/2.20=0.4545; sum=1.0101
    fair = remove_overround({"A": 1.80, "B": 2.20})
    assert fair["A"] == pytest.approx(0.5556 / 1.0101, abs=1e-3)
    assert fair["B"] == pytest.approx(0.4545 / 1.0101, abs=1e-3)


def test_remove_overround_requires_at_least_two_outcomes():
    with pytest.raises(ValueError):
        remove_overround({"A": 1.85})
