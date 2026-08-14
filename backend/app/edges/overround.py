"""Bookmaker overround (vig/margin) math — pure functions, no DB access.

A bookmaker's decimal odds imply a probability (1/price) for each outcome,
but those implied probabilities always sum to *more* than 1.0 — the excess
is the bookmaker's margin. Comparing a model's probability directly against
a single raw implied probability therefore always makes the model look
worse than it is; removing the overround first (de-vigging) is what makes
the comparison honest.

Correct de-vigging needs both sides of the SAME bookmaker's book — the
margin is a property of one bookmaker's simultaneous prices, not something
you can compute by mixing one bookmaker's price on one side with a
different bookmaker's price on the other.
"""


def implied_probability(price_decimal: float) -> float:
    if price_decimal <= 1.0:
        raise ValueError(f"price_decimal must be > 1.0, got {price_decimal!r}")
    return 1.0 / price_decimal


def overround(prices: dict[str, float]) -> float:
    """The bookmaker's margin, e.g. 0.05 = a 5% overround."""
    return sum(implied_probability(p) for p in prices.values()) - 1.0


def remove_overround(prices: dict[str, float]) -> dict[str, float]:
    """Proportional (multiplicative) de-vig: scales each implied probability
    down by the same factor so they sum to exactly 1.0. The standard,
    simplest method — not Shin's method or the power method, which model
    *why* the margin exists (informed-money risk) rather than just removing
    it proportionally. Reasonable starting point; revisit if backtesting
    (Stage 1.7) shows it's systematically off for certain market shapes.
    """
    if len(prices) < 2:
        raise ValueError("remove_overround needs at least two outcomes to de-vig against each other")
    implied = {selection: implied_probability(price) for selection, price in prices.items()}
    total = sum(implied.values())
    return {selection: prob / total for selection, prob in implied.items()}
