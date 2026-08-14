from app.edges.calculator import MarketEdge, best_edge


def _edge(**overrides) -> MarketEdge:
    defaults = dict(
        match_id=1,
        odds_quote_id=1,
        market_type="h2h",
        selection="Carlton",
        line_value=None,
        bookmaker_name="Sportsbet",
        price_decimal=1.85,
        model_probability=0.6,
        secondary_model_probability=None,
        market_implied_probability=0.55,
        fair_market_probability=0.55,
        overround_removed=True,
        fair_odds=1.67,
        model_edge=0.05,
        expected_value=0.02,
        edge_tier="moderate",
        confidence_tier="moderate",
        confidence_reasons=[],
    )
    defaults.update(overrides)
    return MarketEdge(**defaults)


def test_best_edge_none_for_empty_list():
    assert best_edge([]) is None


def test_best_edge_prefers_higher_confidence_over_bigger_raw_edge():
    weak_but_confident = _edge(odds_quote_id=1, edge_tier="weak", confidence_tier="higher", expected_value=0.01)
    strong_but_unconfident = _edge(odds_quote_id=2, edge_tier="strong", confidence_tier="insufficient_data", expected_value=0.20)

    assert best_edge([weak_but_confident, strong_but_unconfident]) is weak_but_confident


def test_best_edge_prefers_bigger_edge_within_same_confidence():
    smaller = _edge(odds_quote_id=1, edge_tier="weak", confidence_tier="moderate")
    bigger = _edge(odds_quote_id=2, edge_tier="strong", confidence_tier="moderate")

    assert best_edge([smaller, bigger]) is bigger


def test_best_edge_tiebreaks_on_expected_value():
    lower_ev = _edge(odds_quote_id=1, edge_tier="moderate", confidence_tier="moderate", expected_value=0.01)
    higher_ev = _edge(odds_quote_id=2, edge_tier="moderate", confidence_tier="moderate", expected_value=0.05)

    assert best_edge([lower_ev, higher_ev]) is higher_ev


def test_best_edge_single_edge_returned_even_if_insufficient_data():
    only = _edge(confidence_tier="insufficient_data", edge_tier="none")
    assert best_edge([only]) is only
