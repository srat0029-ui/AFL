"""Transparent, multi-factor "opportunity score" for ranking normalized
player-prop markets (Sections 19-20 of the automated-odds stage brief):
"Do not rank solely by EV... Keep the components visible/explainable... Do
not invent a magical opaque 'bet score.'"

Every component is a named, independently-inspectable number; the total is
their sum (optionally scaled down by a single, also-visible penalty
multiplier for uncertain participation). Nothing here is fit to data or
tuned against outcomes — these are fixed, documented weights reflecting
this stage's explicit priorities (model-market difference and EV matter,
but confidence/lineup-certainty/freshness/calibration-quality should be
able to override a large apparent EV from a low-trust source — Section
20's "Lower confidence + large apparent EV: Should be downgraded and
prominently warned").
"""

from dataclasses import dataclass, field

from app.player_modelling.prop_odds_freshness import FRESHNESS_AGING, FRESHNESS_FRESH, FRESHNESS_STALE

_CONFIDENCE_POINTS = {
    "higher_confidence": 20.0,
    "moderate_confidence": 12.0,
    "lower_confidence": 5.0,
    "insufficient_history": 0.0,
}
_FRESHNESS_POINTS = {
    FRESHNESS_FRESH: 10.0,
    FRESHNESS_AGING: 4.0,
    FRESHNESS_STALE: 0.0,
}

# Uncertain-participation and stale-price penalties are multiplicative and
# visible as their own component (not folded silently into the total) -
# Section 20's explicit examples ("uncertain lineup" / "stale odds ...
# should not be treated as currently actionable") apply regardless of how
# large the raw difference/EV components are.
UNCERTAIN_PARTICIPATION_MULTIPLIER = 0.5
STALE_PRICE_MULTIPLIER = 0.25


@dataclass(frozen=True)
class OpportunityScore:
    total: float
    difference_component: float
    ev_component: float
    confidence_component: float
    freshness_component: float
    lineup_component: float
    calibration_component: float
    penalty_multiplier: float
    penalty_reasons: list[str] = field(default_factory=list)


def compute_opportunity_score(
    *,
    difference_pp: float,
    expected_value: float,
    confidence_tier: str,
    is_uncertain_participation: bool,
    freshness: str,
    has_calibration_data: bool,
) -> OpportunityScore:
    # Difference and EV are both capped at a generous "this is already a
    # very large apparent edge" reference point (15pp / 30% EV) so one
    # extreme outlier quote can't dominate the ranking by an arbitrary
    # amount - the cap value itself is visible here, not hidden in a
    # normalization step done elsewhere.
    difference_component = min(max(difference_pp, 0.0) / 0.15, 1.0) * 40.0
    ev_component = min(max(expected_value, 0.0) / 0.30, 1.0) * 25.0
    confidence_component = _CONFIDENCE_POINTS.get(confidence_tier, 0.0)
    freshness_component = _FRESHNESS_POINTS.get(freshness, 0.0)
    lineup_component = 0.0 if is_uncertain_participation else 5.0
    calibration_component = 5.0 if has_calibration_data else 0.0

    raw_total = (
        difference_component + ev_component + confidence_component
        + freshness_component + lineup_component + calibration_component
    )

    multiplier = 1.0
    reasons: list[str] = []
    if is_uncertain_participation:
        multiplier *= UNCERTAIN_PARTICIPATION_MULTIPLIER
        reasons.append("uncertain participation")
    if freshness == FRESHNESS_STALE:
        multiplier *= STALE_PRICE_MULTIPLIER
        reasons.append("stale price")

    return OpportunityScore(
        total=raw_total * multiplier,
        difference_component=difference_component,
        ev_component=ev_component,
        confidence_component=confidence_component,
        freshness_component=freshness_component,
        lineup_component=lineup_component,
        calibration_component=calibration_component,
        penalty_multiplier=multiplier,
        penalty_reasons=reasons,
    )
