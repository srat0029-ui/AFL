"""Tests for quality/readiness tiers (Market Integrity stage, Section 9) —
never "safe"/"lock"/"guaranteed", and the hard "do not headline" gates."""

from app.player_modelling.quality_tiers import (
    TIER_DO_NOT_HEADLINE,
    TIER_LABELS,
    TIER_SPECULATIVE,
    TIER_STRONG_CANDIDATE,
    TIER_WORTH_REVIEWING,
    compute_quality_tier,
)


def _base_opportunity(**overrides):
    o = {
        "opportunity_type": "player",
        "odds_freshness": "fresh",
        "confidence_tier": "higher_confidence",
        "edge_category": "moderate_difference",
        "difference_pp": 0.08,
        "is_confirmed": True,
        "selection_status": "confirmed_selected",
        "n_bookmakers": 5,
        "eligible_price_available": True,
        "price_integrity": None,
        "warnings": [],
        "best_price_all_differs_from_enabled": False,
    }
    o.update(overrides)
    return o


def test_never_uses_forbidden_words_in_tier_labels():
    forbidden = {"safe", "lock", "guaranteed"}
    for label in TIER_LABELS.values():
        assert not any(word in label.lower() for word in forbidden)


def test_strong_candidate_when_all_signals_favourable():
    result = compute_quality_tier(_base_opportunity())
    assert result.tier == TIER_STRONG_CANDIDATE


def test_do_not_headline_when_stale():
    result = compute_quality_tier(_base_opportunity(odds_freshness="stale"))
    assert result.tier == TIER_DO_NOT_HEADLINE
    assert "stale" in result.caveats[0].lower()


def test_do_not_headline_when_insufficient_history():
    result = compute_quality_tier(_base_opportunity(confidence_tier="insufficient_history"))
    assert result.tier == TIER_DO_NOT_HEADLINE


def test_do_not_headline_when_player_confirmed_out():
    result = compute_quality_tier(_base_opportunity(selection_status="confirmed_out"))
    assert result.tier == TIER_DO_NOT_HEADLINE


def test_do_not_headline_when_no_eligible_price():
    result = compute_quality_tier(_base_opportunity(eligible_price_available=False))
    assert result.tier == TIER_DO_NOT_HEADLINE


def test_do_not_headline_when_price_integrity_fails():
    result = compute_quality_tier(
        _base_opportunity(price_integrity={"passes_integrity": False, "issues": ["stale next-best"]})
    )
    assert result.tier == TIER_DO_NOT_HEADLINE


def test_worth_reviewing_when_single_bookmaker_only():
    result = compute_quality_tier(_base_opportunity(n_bookmakers=1))
    assert result.tier == TIER_WORTH_REVIEWING
    assert any("one bookmaker" in c for c in result.caveats)


def test_worth_reviewing_when_lower_confidence():
    result = compute_quality_tier(_base_opportunity(confidence_tier="lower_confidence"))
    assert result.tier in (TIER_WORTH_REVIEWING, TIER_SPECULATIVE)


def test_speculative_when_no_meaningful_difference():
    result = compute_quality_tier(_base_opportunity(edge_category="no_meaningful_difference", difference_pp=-0.01))
    assert result.tier == TIER_SPECULATIVE


def test_speculative_when_unconfirmed_player_and_no_meaningful_edge():
    result = compute_quality_tier(
        _base_opportunity(is_confirmed=False, selection_status="uncertain", edge_category="no_meaningful_difference", difference_pp=-0.01)
    )
    assert result.tier == TIER_SPECULATIVE
    assert any("not confirmed" in c for c in result.caveats)


def test_team_opportunity_not_gated_by_confirmed_lineup():
    result = compute_quality_tier(_base_opportunity(opportunity_type="team", is_confirmed=None, selection_status=None))
    assert result.tier == TIER_STRONG_CANDIDATE
