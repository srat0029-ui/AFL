"""Model-vs-market comparison math for manually-entered player-prop offers
— Sections 12-15 of the live-projection brief. Reuses app/edges/overround.py
and app/edges/fair_odds.py exactly as-is (the same de-vig/fair-odds/EV math
already validated for team markets) rather than re-deriving it for player
props — the maths (implied probability, proportional de-vig, fair odds,
expected value) doesn't care whether the underlying event is a team win or
a player crossing a disposal/goal line.
"""

from dataclasses import dataclass

from app.edges.fair_odds import expected_value as _expected_value
from app.edges.fair_odds import fair_odds_from_probability
from app.edges.overround import implied_probability, remove_overround


@dataclass(frozen=True)
class MarketComparison:
    model_probability: float
    model_fair_odds: float
    offered_odds: float
    raw_implied_probability: float
    # None when only one side of the market was quoted - Section 12
    # explicitly asks this be labelled clearly rather than pretending vig
    # has been removed when it hasn't.
    devigged_probability: float | None
    overround_removed: bool
    difference_pp: float  # model_probability - (devigged_probability or raw_implied_probability), in probability points (0.104 = +10.4pp)
    expected_value: float  # model-estimated EV per $1 staked at offered_odds - NOT guaranteed profit


def compare_model_to_market(
    model_probability: float, offered_odds: float, opposite_side_odds: float | None = None
) -> MarketComparison:
    """`opposite_side_odds` is the other side of the SAME bookmaker's book
    for the SAME line (e.g. the "under 29.5" price when `offered_odds` is
    "over 29.5") - only pass it when that side was actually quoted. Most
    alternate goal lines (e.g. "2+ goals") only ever have one side quoted in
    practice, so `devigged_probability` stays None and callers must not
    present `raw_implied_probability` as though vig had been removed."""
    raw_implied = implied_probability(offered_odds)

    devigged: float | None = None
    overround_removed = False
    if opposite_side_odds is not None:
        fair = remove_overround({"this": offered_odds, "other": opposite_side_odds})
        devigged = fair["this"]
        overround_removed = True

    market_reference = devigged if devigged is not None else raw_implied
    return MarketComparison(
        model_probability=model_probability,
        model_fair_odds=fair_odds_from_probability(model_probability),
        offered_odds=offered_odds,
        raw_implied_probability=raw_implied,
        devigged_probability=devigged,
        overround_removed=overround_removed,
        difference_pp=model_probability - market_reference,
        expected_value=_expected_value(model_probability, offered_odds),
    )


# --- Edge-quality categories (Section 15) ---------------------------------
#
# Deliberately configurable, not presented as scientifically validated
# cutoffs - these are round-number starting points, not a backtested
# threshold-selection result (there is no historical player-prop odds
# archive to validate them against yet - see the "Betting profitability"
# section of the disposal/goal Model Research pages for why). A stronger
# label additionally REQUIRES sufficient model confidence: the same +10
# percentage-point gap is capped at "small" for a Lower/Insufficient-history
# projection, and can only reach "larger" for a Higher-confidence one.

EdgeCategory = str  # "no_meaningful_difference" | "small_difference" | "moderate_difference" | "larger_difference"

_CATEGORY_ORDER = ["no_meaningful_difference", "small_difference", "moderate_difference", "larger_difference"]

# Confidence tiers cap how strong a label the SAME numeric gap is allowed to
# display as - Section 15's explicit "+10pp with Lower confidence should not
# look the same as +10pp with Higher confidence" requirement.
_CONFIDENCE_CAP = {
    "insufficient_history": "small_difference",
    "lower_confidence": "small_difference",
    "moderate_confidence": "moderate_difference",
    "higher_confidence": "larger_difference",
}


@dataclass(frozen=True)
class EdgeCategoryThresholds:
    small: float = 0.03
    moderate: float = 0.06
    larger: float = 0.10


DEFAULT_EDGE_THRESHOLDS = EdgeCategoryThresholds()


def categorize_edge(
    difference_pp: float, confidence_tier: str, thresholds: EdgeCategoryThresholds = DEFAULT_EDGE_THRESHOLDS
) -> EdgeCategory:
    abs_diff = abs(difference_pp)
    if abs_diff < thresholds.small:
        return "no_meaningful_difference"
    if abs_diff < thresholds.moderate:
        raw = "small_difference"
    elif abs_diff < thresholds.larger:
        raw = "moderate_difference"
    else:
        raw = "larger_difference"

    cap = _CONFIDENCE_CAP.get(confidence_tier, "small_difference")
    return raw if _CATEGORY_ORDER.index(raw) <= _CATEGORY_ORDER.index(cap) else cap
