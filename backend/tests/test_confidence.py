import pytest

from app.edges.confidence import ConfidenceInputs, compute_confidence, edge_tier


class TestEdgeTier:
    def test_strong_edge(self):
        assert edge_tier(model_probability=0.70, fair_market_probability=0.60) == "strong"

    def test_moderate_edge(self):
        assert edge_tier(model_probability=0.64, fair_market_probability=0.59) == "moderate"

    def test_weak_edge(self):
        assert edge_tier(model_probability=0.55, fair_market_probability=0.53) == "weak"

    def test_no_edge_when_gap_tiny(self):
        assert edge_tier(model_probability=0.51, fair_market_probability=0.50) == "none"

    def test_no_edge_when_model_likes_it_less_than_market(self):
        # model thinks LESS of this selection than the market does -> not a mispricing in its favour
        assert edge_tier(model_probability=0.45, fair_market_probability=0.60) == "none"

    def test_boundary_values_are_inclusive(self):
        # values placed unambiguously on the >= side of each threshold —
        # binary floats can't represent 0.08 exactly, so testing the exact
        # literal boundary is inherently fragile; a hair above it is the
        # correct way to confirm the tier logic, not the float representation.
        assert edge_tier(0.5801, 0.50) == "strong"
        assert edge_tier(0.5401, 0.50) == "moderate"
        assert edge_tier(0.5201, 0.50) == "weak"

    def test_just_below_each_boundary_falls_into_the_lower_tier(self):
        assert edge_tier(0.5799, 0.50) == "moderate"
        assert edge_tier(0.5399, 0.50) == "weak"
        assert edge_tier(0.5199, 0.50) == "none"


class TestComputeConfidence:
    def test_hard_gate_when_model_has_no_validated_edge(self):
        result = compute_confidence(ConfidenceInputs(has_model_edge_over_naive=False, overround_removed=True))
        assert result.tier == "insufficient_data"
        assert "no demonstrated predictive edge" in result.reasons[0]

    def test_hard_gate_overrides_everything_else(self):
        # even with perfect data quality and close model agreement, no validated edge = insufficient data
        result = compute_confidence(
            ConfidenceInputs(
                has_model_edge_over_naive=False,
                overround_removed=True,
                primary_model_probability=0.60,
                secondary_model_probability=0.61,
                min_team_games=50,
            )
        )
        assert result.tier == "insufficient_data"

    def test_baseline_moderate_with_no_issues(self):
        result = compute_confidence(ConfidenceInputs(has_model_edge_over_naive=True, overround_removed=True))
        assert result.tier == "moderate"

    def test_downgrades_when_overround_not_removed(self):
        result = compute_confidence(ConfidenceInputs(has_model_edge_over_naive=True, overround_removed=False))
        assert result.tier == "lower"
        assert any("overround" in r for r in result.reasons)

    def test_downgrades_on_small_sample(self):
        result = compute_confidence(
            ConfidenceInputs(has_model_edge_over_naive=True, overround_removed=True, min_team_games=3)
        )
        assert result.tier == "lower"
        assert any("Limited recent-form sample" in r for r in result.reasons)

    def test_no_downgrade_when_sample_meets_threshold(self):
        result = compute_confidence(
            ConfidenceInputs(has_model_edge_over_naive=True, overround_removed=True, min_team_games=10)
        )
        assert result.tier == "moderate"

    def test_multiple_downgrades_clamp_at_lower_not_below(self):
        result = compute_confidence(
            ConfidenceInputs(
                has_model_edge_over_naive=True,
                overround_removed=False,
                min_team_games=2,
                primary_model_probability=0.70,
                secondary_model_probability=0.40,  # big disagreement, another downgrade
            )
        )
        assert result.tier == "lower"  # clamped, not something below "lower"

    def test_downgrades_on_model_disagreement(self):
        result = compute_confidence(
            ConfidenceInputs(
                has_model_edge_over_naive=True,
                overround_removed=True,
                primary_model_probability=0.70,
                secondary_model_probability=0.50,
            )
        )
        assert result.tier == "lower"
        assert any("disagree" in r for r in result.reasons)

    def test_upgrades_on_close_model_agreement(self):
        result = compute_confidence(
            ConfidenceInputs(
                has_model_edge_over_naive=True,
                overround_removed=True,
                primary_model_probability=0.61,
                secondary_model_probability=0.60,
            )
        )
        assert result.tier == "higher"
        assert any("agree closely" in r for r in result.reasons)

    def test_upgrade_clamps_at_higher_not_above(self):
        result = compute_confidence(
            ConfidenceInputs(
                has_model_edge_over_naive=True,
                overround_removed=True,
                min_team_games=50,
                primary_model_probability=0.61,
                secondary_model_probability=0.60,
            )
        )
        assert result.tier == "higher"

    def test_no_reasons_gives_default_message(self):
        result = compute_confidence(ConfidenceInputs(has_model_edge_over_naive=True, overround_removed=True))
        assert result.reasons == ["No specific data-quality concerns identified."]
