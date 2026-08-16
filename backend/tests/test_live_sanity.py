"""Tests for live_sanity.py — Section 16's "sanity-check failures"
requirement: each check must actually catch the anomaly it claims to,
and pass clean, plausible projections through untouched.
"""

from app.player_modelling.live_engine import DisposalProjectionResult, GoalProjectionResult, LiveProjectionRun
from app.player_modelling.live_sanity import (
    check_disposal_projection,
    check_goal_projection,
    check_no_projection_for_confirmed_out,
    run_all_sanity_checks,
)


def _disposal(mean=18.0, alpha=3.0, player_id=1, match_id=100):
    return DisposalProjectionResult(
        player_id=player_id, match_id=match_id, team_id=10, lineup_status="expected_in", games_of_history=40,
        predicted_mean=mean, nb_alpha=alpha, confidence_tier="higher_confidence", warnings=[], input_features={},
    )


def _goal(mean=0.6, distribution_kind="nb", nb_alpha=1.5, p_score=None, mu_scored=None, alpha_scored=None, player_id=1, match_id=100):
    return GoalProjectionResult(
        player_id=player_id, match_id=match_id, team_id=10, lineup_status="expected_in", games_of_history=40,
        predicted_mean=mean, distribution_kind=distribution_kind, nb_alpha=nb_alpha, p_score=p_score, mu_scored=mu_scored,
        alpha_scored=alpha_scored, scoring_archetype="regular", confidence_tier="higher_confidence", warnings=[], input_features={},
    )


def _empty_run(disposal_projections=(), goal_projections=()):
    from datetime import datetime, timezone

    return LiveProjectionRun(
        upcoming_matches=[], expected_players=[], disposal_run=None, goal_run=None, disposal_model_version=None,
        goal_model_version=None, team_model_version=None, data_cutoff=None, generated_at=datetime.now(timezone.utc),
        disposal_projections=list(disposal_projections), goal_projections=list(goal_projections),
    )


# --- disposal checks ---


def test_disposal_plausible_projection_has_no_anomalies():
    assert check_disposal_projection(_disposal(mean=20.0, alpha=3.0)) == []


def test_disposal_implausible_mean_flagged():
    anomalies = check_disposal_projection(_disposal(mean=200.0, alpha=3.0))
    assert any(a.category == "implausible_mean" for a in anomalies)


def test_disposal_negative_mean_flagged():
    anomalies = check_disposal_projection(_disposal(mean=-5.0, alpha=3.0))
    assert any(a.category == "implausible_mean" for a in anomalies)


# --- goal checks ---


def test_goal_plausible_nb_projection_has_no_anomalies():
    assert check_goal_projection(_goal(mean=0.5, distribution_kind="nb", nb_alpha=1.2)) == []


def test_goal_plausible_hurdle_projection_has_no_anomalies():
    anomalies = check_goal_projection(_goal(mean=1.0, distribution_kind="hurdle", p_score=0.5, mu_scored=1.8, alpha_scored=1.0))
    assert anomalies == []


def test_goal_implausible_mean_flagged():
    anomalies = check_goal_projection(_goal(mean=50.0, distribution_kind="nb", nb_alpha=1.2))
    assert any(a.category == "implausible_mean" for a in anomalies)


# --- confirmed-out check ---


def test_no_confirmed_out_projection_passes_when_none_present():
    run = _empty_run(disposal_projections=[_disposal(player_id=1, match_id=100)])
    assert check_no_projection_for_confirmed_out(run, {100: {999}}) == []  # player 999 is confirmed out, but wasn't projected


def test_confirmed_out_projection_is_flagged_as_defense_in_depth():
    """Structurally this should never happen (load_expected_players
    excludes expected_out players), but the check must actually detect it
    if it somehow did - see live_sanity.py's docstring."""
    run = _empty_run(disposal_projections=[_disposal(player_id=1, match_id=100)], goal_projections=[_goal(player_id=1, match_id=100)])
    anomalies = check_no_projection_for_confirmed_out(run, {100: {1}})
    categories = [a.category for a in anomalies]
    assert categories.count("projection_for_confirmed_out") == 2  # both disposal and goal projections flagged


def test_run_all_sanity_checks_aggregates_everything():
    run = _empty_run(
        disposal_projections=[_disposal(mean=999.0, player_id=1, match_id=100)],
        goal_projections=[_goal(mean=0.4, player_id=2, match_id=100)],
    )
    anomalies = run_all_sanity_checks(run, {100: {2}})
    categories = {a.category for a in anomalies}
    assert "implausible_mean" in categories
    assert "projection_for_confirmed_out" in categories
