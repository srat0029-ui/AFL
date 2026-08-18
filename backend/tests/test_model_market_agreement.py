"""Tests for model vs market direction agreement (Weekly Bet Review
stage, Section 5)."""

from app.player_modelling.model_market_agreement import (
    AGREES_ON_DIRECTION,
    DISAGREES_ON_DIRECTION,
    classify_direction_agreement,
)


def test_real_collingwood_case_agrees_on_direction():
    # Model 46.2%, market ~22.4% - both underdog, differ on degree.
    result = classify_direction_agreement(0.462, 0.224)
    assert result.classification == AGREES_ON_DIRECTION
    assert result.model_favours_selection is False
    assert result.market_favours_selection is False


def test_both_favour_agrees_on_direction():
    result = classify_direction_agreement(0.70, 0.55)
    assert result.classification == AGREES_ON_DIRECTION


def test_model_favours_market_does_not_disagrees():
    result = classify_direction_agreement(0.633, 0.497)
    assert result.classification == DISAGREES_ON_DIRECTION
    assert result.model_favours_selection is True
    assert result.market_favours_selection is False


def test_market_favours_model_does_not_disagrees():
    result = classify_direction_agreement(0.30, 0.60)
    assert result.classification == DISAGREES_ON_DIRECTION


def test_description_mentions_both_probabilities():
    result = classify_direction_agreement(0.462, 0.224)
    assert "46.2%" in result.description
    assert "22.4%" in result.description
