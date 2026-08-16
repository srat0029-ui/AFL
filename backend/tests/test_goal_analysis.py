"""Tests for goal_analysis.py's team-goal-consistency and ranking-quality
calculations — Section 27's "team-goal reconciliation calculations" and
Section 14's ranking evaluation.
"""

from app.player_modelling.goal_analysis import evaluate_ranking_quality, measure_team_goal_consistency
from app.player_modelling.goal_backtest import GoalPredictionRecord
from app.player_modelling.goal_features import GoalFeatureRow


def _pred(player_id, match_id, team_id, predicted_mean, actual):
    return GoalPredictionRecord(
        player_id=player_id, match_id=match_id, team_id=team_id, season_year=2020, is_final=False,
        games_of_history=20, tog_last5_avg=80.0, zero_goal_rate_last10=0.5, actual=actual,
        predicted_mean=predicted_mean, distribution_kind="nb", nb_alpha=1.0,
    )


def test_team_goal_consistency_measures_a_known_gap():
    predictions = [
        _pred(1, 100, team_id=10, predicted_mean=2.0, actual=2),
        _pred(2, 100, team_id=10, predicted_mean=1.5, actual=1),
        _pred(3, 100, team_id=10, predicted_mean=1.0, actual=1),
    ]
    eval_rows = [
        GoalFeatureRow(player_id=1, match_id=100, team_id=10, opponent_team_id=20, season_year=2020, round_number=1, is_final=False, scheduled_start=None, goals=2, games_of_history=20, features={}),
        GoalFeatureRow(player_id=2, match_id=100, team_id=10, opponent_team_id=20, season_year=2020, round_number=1, is_final=False, scheduled_start=None, goals=1, games_of_history=20, features={}),
        GoalFeatureRow(player_id=3, match_id=100, team_id=10, opponent_team_id=20, season_year=2020, round_number=1, is_final=False, scheduled_start=None, goals=1, games_of_history=20, features={}),
    ]
    # sum of predicted = 4.5 goals; team expected_score = 60 points = 10 goals -> gap = 5.5
    team_context = {100: {10: {"expected_score": 60.0}}}
    result = measure_team_goal_consistency(predictions, eval_rows, team_context)
    assert result.n_team_matches == 1
    assert result.mean_sum_predicted == 4.5
    assert result.mean_team_expected_goals == 10.0
    assert result.mean_signed_gap == 4.5 - 10.0
    assert result.mean_absolute_gap == abs(4.5 - 10.0)


def test_team_goal_consistency_skips_matches_with_no_team_context():
    predictions = [_pred(1, 100, team_id=10, predicted_mean=1.0, actual=1)]
    eval_rows = [
        GoalFeatureRow(player_id=1, match_id=100, team_id=10, opponent_team_id=20, season_year=2020, round_number=1, is_final=False, scheduled_start=None, goals=1, games_of_history=20, features={}),
    ]
    result = measure_team_goal_consistency(predictions, eval_rows, team_context={})
    assert result.n_team_matches == 0


def test_ranking_quality_perfect_ranking_scores_one():
    predictions = [
        _pred(1, 100, team_id=10, predicted_mean=3.0, actual=3),  # top scorer, top projection
        _pred(2, 100, team_id=10, predicted_mean=2.0, actual=2),
        _pred(3, 100, team_id=10, predicted_mean=1.0, actual=1),
        _pred(4, 100, team_id=10, predicted_mean=0.1, actual=0),
    ]
    result = evaluate_ranking_quality(predictions)
    assert result.n_matches == 1
    assert result.top1_hit_rate == 1.0
    assert result.top2_capture_rate == 1.0
    assert result.top3_capture_rate == 1.0


def test_ranking_quality_worst_case_ranking_scores_zero_for_top1():
    predictions = [
        _pred(1, 100, team_id=10, predicted_mean=0.1, actual=3),  # actual top scorer, ranked last
        _pred(2, 100, team_id=10, predicted_mean=3.0, actual=0),  # top projection, didn't score
        _pred(3, 100, team_id=10, predicted_mean=2.0, actual=0),
        _pred(4, 100, team_id=10, predicted_mean=1.0, actual=0),
    ]
    result = evaluate_ranking_quality(predictions)
    assert result.top1_hit_rate == 0.0


def test_ranking_quality_skips_matches_with_too_few_players():
    predictions = [_pred(1, 100, team_id=10, predicted_mean=1.0, actual=1), _pred(2, 100, team_id=10, predicted_mean=0.5, actual=0)]
    result = evaluate_ranking_quality(predictions)
    assert result.n_matches == 0  # fewer than 3 players in the match - skipped, not divided by zero
