"""Closing-line-value arithmetic — pure functions, no DB access.

CLV asks whether the price taken at entry was better than the price the
market settled on by the time it closed. It is the fastest honest signal of
betting edge: win/loss on a few hundred props is mostly noise, whereas
consistently beating the close is evidence the entry-time belief contained
information the market only priced in later.

Two views, both computed from values frozen on the prediction row (never
re-derived from today's market):

- price CLV: entry price vs the SAME bookmaker's closing price for the SAME
  line and side. Only defined when that exact line was still quoted at the
  close — a price for "over 24.5" is not comparable to a price for "over
  25.5", so a moved line yields None rather than a misleading number.
- probability CLV: the closing de-vigged consensus probability for the
  selection vs the break-even probability implied by the entry price.
  Positive means the closing market thought the selection more likely than
  the entry price needed it to be.
"""

from app.edges.overround import implied_probability


def price_clv(entry_price: float, closing_price: float | None) -> float | None:
    """Relative price advantage over the close: entry / closing - 1.
    +0.05 means the entry price paid 5% more than the closing price."""
    if closing_price is None:
        return None
    if entry_price <= 1.0 or closing_price <= 1.0:
        raise ValueError(f"decimal prices must be > 1.0, got entry={entry_price!r} closing={closing_price!r}")
    return entry_price / closing_price - 1.0


def probability_clv(entry_price: float, closing_fair_probability: float | None) -> float | None:
    """Closing fair probability minus the entry price's break-even
    probability, in probability points (0.02 = +2pp)."""
    if closing_fair_probability is None:
        return None
    if not (0.0 <= closing_fair_probability <= 1.0):
        raise ValueError(f"closing_fair_probability must be in [0, 1], got {closing_fair_probability!r}")
    return closing_fair_probability - implied_probability(entry_price)


def line_moved(entry_line: float, closing_line: float | None) -> bool | None:
    """Whether the bookmaker's main line at the close differs from the line
    taken at entry. None when no closing main line was captured."""
    if closing_line is None:
        return None
    return closing_line != entry_line
