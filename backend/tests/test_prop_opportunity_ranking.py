from app.player_modelling.prop_odds_freshness import FRESHNESS_AGING, FRESHNESS_FRESH, FRESHNESS_STALE
from app.player_modelling.prop_opportunity_ranking import compute_opportunity_score


def _score(**overrides):
    defaults = dict(
        difference_pp=0.05,
        expected_value=0.10,
        confidence_tier="higher_confidence",
        is_uncertain_participation=False,
        freshness=FRESHNESS_FRESH,
        has_calibration_data=True,
    )
    defaults.update(overrides)
    return compute_opportunity_score(**defaults)


def test_all_components_are_visible_and_sum_to_total_before_penalty():
    score = _score()
    component_sum = (
        score.difference_component + score.ev_component + score.confidence_component
        + score.freshness_component + score.lineup_component + score.calibration_component
    )
    assert score.total == component_sum * score.penalty_multiplier


def test_higher_difference_and_ev_increase_score():
    low = _score(difference_pp=0.01, expected_value=0.02)
    high = _score(difference_pp=0.14, expected_value=0.29)
    assert high.total > low.total


def test_negative_difference_and_ev_contribute_nothing_not_negative():
    score = _score(difference_pp=-0.10, expected_value=-0.20)
    assert score.difference_component == 0.0
    assert score.ev_component == 0.0


def test_confidence_tiers_ranked_correctly():
    higher = _score(confidence_tier="higher_confidence")
    moderate = _score(confidence_tier="moderate_confidence")
    lower = _score(confidence_tier="lower_confidence")
    insufficient = _score(confidence_tier="insufficient_history")
    assert higher.confidence_component > moderate.confidence_component > lower.confidence_component > insufficient.confidence_component


def test_uncertain_participation_applies_visible_penalty_and_reduces_score():
    certain = _score(is_uncertain_participation=False)
    uncertain = _score(is_uncertain_participation=True)
    assert uncertain.penalty_multiplier == 0.5
    assert "uncertain participation" in uncertain.penalty_reasons
    assert certain.penalty_multiplier == 1.0
    assert certain.penalty_reasons == []
    # lineup_component itself also drops to 0 (a real, visible component -
    # not just folded into the multiplier), on top of the 0.5x penalty.
    assert uncertain.lineup_component == 0.0
    assert certain.lineup_component > 0.0
    assert uncertain.total < certain.total * 0.5


def test_stale_price_applies_heavy_penalty():
    fresh = _score(freshness=FRESHNESS_FRESH)
    stale = _score(freshness=FRESHNESS_STALE)
    assert stale.penalty_multiplier == 0.25
    assert "stale price" in stale.penalty_reasons
    assert stale.total < fresh.total


def test_uncertain_and_stale_penalties_compound():
    both = _score(is_uncertain_participation=True, freshness=FRESHNESS_STALE)
    assert both.penalty_multiplier == 0.5 * 0.25
    assert set(both.penalty_reasons) == {"uncertain participation", "stale price"}


def test_a_large_apparent_ev_from_low_confidence_can_be_outranked_by_smaller_high_confidence_edge():
    """Section 20's explicit example: a big apparent EV from a low-trust
    source should not automatically rank above a smaller, well-supported
    one — verify the scoring doesn't let EV alone dominate."""
    big_ev_low_confidence = _score(
        difference_pp=0.03, expected_value=0.28, confidence_tier="insufficient_history",
        is_uncertain_participation=True, freshness=FRESHNESS_AGING, has_calibration_data=False,
    )
    modest_ev_high_confidence = _score(
        difference_pp=0.08, expected_value=0.12, confidence_tier="higher_confidence",
        is_uncertain_participation=False, freshness=FRESHNESS_FRESH, has_calibration_data=True,
    )
    assert modest_ev_high_confidence.total > big_ev_low_confidence.total
