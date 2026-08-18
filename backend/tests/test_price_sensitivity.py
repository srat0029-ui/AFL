"""Tests for price sensitivity + model fair price (Weekly Bet Review
stage, Section 7)."""

from app.player_modelling.price_sensitivity import compute_price_sensitivity


def _book(name, price, eligibility="included"):
    return {"bookmaker_name": name, "price_decimal": price, "eligibility": eligibility}


def test_model_fair_price_is_inverse_of_probability():
    s = compute_price_sensitivity(0.5, [_book("A", 2.0)])
    assert s.model_fair_price == 2.0


def test_price_points_sorted_best_price_first():
    s = compute_price_sensitivity(0.5, [_book("A", 1.8), _book("B", 2.2), _book("C", 2.0)])
    prices = [p.price_decimal for p in s.price_points]
    assert prices == sorted(prices, reverse=True)


def test_ev_increases_with_price():
    s = compute_price_sensitivity(0.5, [_book("A", 1.8), _book("B", 2.2)])
    by_name = {p.bookmaker_name: p.model_estimated_ev for p in s.price_points}
    assert by_name["B"] > by_name["A"]


def test_excludes_exchange_and_excluded_bookmakers():
    s = compute_price_sensitivity(0.5, [_book("A", 2.0), _book("Betfair", 5.0, eligibility="informational_only"), _book("Blacklisted", 3.0, eligibility="excluded")])
    names = {p.bookmaker_name for p in s.price_points}
    assert names == {"A"}


def test_negative_ev_when_price_below_fair():
    s = compute_price_sensitivity(0.5, [_book("A", 1.5)])  # fair price is 2.0
    assert s.price_points[0].model_estimated_ev < 0


def test_positive_ev_when_price_above_fair():
    s = compute_price_sensitivity(0.5, [_book("A", 3.0)])
    assert s.price_points[0].model_estimated_ev > 0
