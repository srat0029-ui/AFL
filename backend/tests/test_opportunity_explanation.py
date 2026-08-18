"""Tests for the deterministic "why the model likes it" explanation
(Section 14 of the best-bets stage brief) - must be built only from
genuinely-stored, same-unit feature comparisons, never invented."""

from app.player_modelling.opportunity_explanation import why_model_likes_it, why_team_edge_exists


def test_disposal_recent_form_above_baseline_is_named():
    features = {
        "disposals_last3_avg": 32.0, "disposals_last5_avg": 31.0, "disposals_ewma": 31.5,
        "disposals_season_avg": 24.0, "disposals_career_avg": 23.0,
    }
    text = why_model_likes_it("player_disposals", difference_pp=0.1, input_features=features)
    assert "above" in text
    assert "recent disposal form" in text
    assert "longer-run baseline" in text


def test_disposal_recent_form_below_baseline_is_named():
    features = {
        "disposals_last3_avg": 18.0, "disposals_last5_avg": 19.0, "disposals_ewma": 18.5,
        "disposals_season_avg": 26.0, "disposals_career_avg": 25.0,
    }
    text = why_model_likes_it("player_disposals", difference_pp=-0.1, input_features=features)
    assert "below" in text
    assert "recent disposal form" in text


def test_opponent_context_named_when_meaningfully_different():
    features = {
        "disposals_last3_avg": 26.0, "disposals_season_avg": 25.5,
        "opponent_disposals_conceded_avg": 400.0, "team_recent_disposals_avg": 350.0,
    }
    text = why_model_likes_it("player_disposals", difference_pp=0.05, input_features=features)
    assert "opponent's disposals conceded" in text
    assert "400.0" in text
    assert "350.0" in text


def test_opponent_context_never_compares_team_total_against_player_baseline():
    # opponent_disposals_conceded_avg is a TEAM-level aggregate (~350+ per
    # game); without team_recent_disposals_avg (the only valid same-unit
    # counterpart) present, it must never be compared against the
    # player's own individual disposal average — that would be a
    # meaningless, wrong-unit claim.
    features = {
        "disposals_last3_avg": 26.0, "disposals_season_avg": 25.9,
        "opponent_disposals_conceded_avg": 400.0,
    }
    text = why_model_likes_it("player_disposals", difference_pp=0.05, input_features=features)
    assert "400.0" not in text
    assert "no single stored feature" in text


def test_no_factors_produces_honest_fallback_not_invented_reason():
    features = {"disposals_last3_avg": 25.0, "disposals_season_avg": 25.1}
    text = why_model_likes_it("player_disposals", difference_pp=0.05, input_features=features)
    assert "no single stored feature" in text


def test_missing_features_never_crashes_and_gives_fallback():
    text = why_model_likes_it("player_disposals", difference_pp=0.02, input_features={})
    assert "no single stored feature" in text


def test_goal_market_uses_goal_specific_keys():
    features = {
        "goals_last3_avg": 2.5, "goals_last5_avg": 2.2, "goals_ewma": 2.4,
        "goals_career_avg": 1.2,
    }
    text = why_model_likes_it("player_goals", difference_pp=0.08, input_features=features)
    assert "goal-scoring form" in text
    assert "above" in text


def test_stance_reflects_difference_pp_sign():
    features = {"disposals_last3_avg": 26.0, "disposals_season_avg": 25.9}
    above = why_model_likes_it("player_disposals", difference_pp=0.1, input_features=features)
    below = why_model_likes_it("player_disposals", difference_pp=-0.1, input_features=features)
    assert "is above the market" in above
    assert "is below the market" in below


def test_unsupported_market_type_falls_back_honestly():
    text = why_model_likes_it("h2h", difference_pp=0.05, input_features={"anything": 1.0})
    assert "no single stored feature" in text


def test_team_edge_explanation_states_direction():
    text = why_team_edge_exists(model_probability=0.62, secondary_model_probability=0.60, fair_market_probability=0.50)
    assert "above" in text
    assert "62%" in text
    assert "agrees closely" in text


def test_team_edge_explanation_flags_model_disagreement():
    text = why_team_edge_exists(model_probability=0.65, secondary_model_probability=0.40, fair_market_probability=0.50)
    assert "disagrees" in text


def test_team_edge_explanation_below_market():
    text = why_team_edge_exists(model_probability=0.40, secondary_model_probability=None, fair_market_probability=0.50)
    assert "below" in text
