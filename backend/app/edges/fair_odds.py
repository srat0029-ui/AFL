"""Fair odds and expected value — pure functions, no DB access."""


def fair_odds_from_probability(probability: float) -> float:
    """The decimal odds a genuinely fair (zero-margin) book would offer for
    an outcome the model thinks has this probability."""
    if not (0.0 < probability <= 1.0):
        raise ValueError(f"probability must be in (0, 1], got {probability!r}")
    return 1.0 / probability


def expected_value(model_probability: float, price_decimal: float) -> float:
    """Expected profit per $1 staked at `price_decimal`, if the model's
    probability is correct. Positive means the price offers more than fair
    value by the model's own estimate; does not by itself mean the model IS
    correct — that's what confidence tiering (app/edges/confidence.py) is
    for.
    """
    if not (0.0 <= model_probability <= 1.0):
        raise ValueError(f"model_probability must be in [0, 1], got {model_probability!r}")
    win_payout = model_probability * (price_decimal - 1.0)
    lose_cost = (1.0 - model_probability) * 1.0
    return win_payout - lose_cost
