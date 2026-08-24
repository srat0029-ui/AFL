"""Team-market internal consistency (item 1's TEAM_MARKET_INTERNAL_INCONSISTENCY):
a genuinely checkable, model-independent fact about a SINGLE bookmaker's own
two-sided quote — home+away (h2h) or over+under (total), at the exact same
line — should imply a combined probability at or above 1.0 (the bookmaker's
built-in margin/overround). A combined probability BELOW 1.0 means that
bookmaker's own two prices are internally arbitrageable; one materially
ABOVE the normal range suggests a stale/mismatched pairing (e.g. comparing
two quotes recorded hours apart) rather than a real live book. Neither
compares this engine's model to anything — it is pure bookmaker-vs-itself
arithmetic, using implied_probability the same way the rest of the app
already does.
"""

from dataclasses import dataclass

from app.edges.overround import implied_probability

# A real sportsbook's own margin is typically ~2-8% (combined implied
# probability 1.02-1.08) on a well-covered AFL market. Below 1.0 is a
# genuine arbitrage against that one book's own two prices. Above this
# ceiling is far outside any normal AFL margin and more likely reflects
# comparing two quotes that aren't really a live matched pair (e.g. one
# side hasn't been refreshed in a while) than a real 20%+ margin.
MIN_PLAUSIBLE_COMBINED = 1.0
MAX_PLAUSIBLE_COMBINED = 1.20


@dataclass(frozen=True)
class PairConsistency:
    bookmaker_name: str
    side_a_price: float
    side_b_price: float
    combined_probability: float
    is_inconsistent: bool
    description: str


def check_pair_consistency(bookmaker_name: str, side_a_price: float, side_b_price: float) -> PairConsistency:
    combined = implied_probability(side_a_price) + implied_probability(side_b_price)
    if combined < MIN_PLAUSIBLE_COMBINED:
        return PairConsistency(
            bookmaker_name=bookmaker_name, side_a_price=side_a_price, side_b_price=side_b_price, combined_probability=combined,
            is_inconsistent=True,
            description=f"{bookmaker_name}'s own two-sided prices imply a combined probability of {combined:.3f} (< 1.0) — internally arbitrageable at this book's own quoted prices.",
        )
    if combined > MAX_PLAUSIBLE_COMBINED:
        return PairConsistency(
            bookmaker_name=bookmaker_name, side_a_price=side_a_price, side_b_price=side_b_price, combined_probability=combined,
            is_inconsistent=True,
            description=f"{bookmaker_name}'s own two-sided prices imply a combined probability of {combined:.3f} (> {MAX_PLAUSIBLE_COMBINED:.2f}) — wider than any normal AFL margin, worth checking these two quotes were actually recorded as a live matched pair.",
        )
    return PairConsistency(bookmaker_name=bookmaker_name, side_a_price=side_a_price, side_b_price=side_b_price, combined_probability=combined, is_inconsistent=False, description="")
