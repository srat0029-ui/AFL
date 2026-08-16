"""Point-in-time correctness tests for goal_features.py — mirrors
test_disposal_features.py's exact leakage-testing pattern, adapted for
goal-specific state (scoring shots, conversion rate, marks inside 50).
"""

from datetime import datetime, timedelta, timezone

from app.player_modelling.goal_data import PlayerGoalGameRow, TeamGoalGameRow
from app.player_modelling.goal_features import GoalFeatureBuilder

BASE_TIME = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _row(
    player_id=1, match_id=1, team_id=10, opponent_team_id=20, season_year=2020, round_number=1,
    is_home=True, venue_id=1, days_offset=0, goals=1, behinds=1, tog=80, marks_inside_50=1, inside_50s=2,
):
    return PlayerGoalGameRow(
        player_id=player_id, match_id=match_id, team_id=team_id, opponent_team_id=opponent_team_id,
        season_year=season_year, round_number=round_number, is_final=False, is_home=is_home, venue_id=venue_id,
        scheduled_start=BASE_TIME + timedelta(days=days_offset), goals=goals, behinds=behinds,
        disposals=15, kicks=8, marks=4, handballs=7, tackles=3, contested_possessions=5,
        uncontested_possessions=6, inside_50s=inside_50s, marks_inside_50=marks_inside_50, goal_assists=0,
        time_on_ground_pct=tog, subbed_on=False, subbed_off=False,
    )


def test_first_game_has_no_history():
    rows = [_row(match_id=1, days_offset=0, goals=3)]
    features = GoalFeatureBuilder().build(rows)
    assert features[0].games_of_history == 0
    assert features[0].features["goals_last3_avg"] is None
    assert features[0].features["goals_career_avg"] is None


def test_features_use_only_strictly_prior_games():
    rows = [
        _row(match_id=1, days_offset=0, goals=0),
        _row(match_id=2, days_offset=7, goals=2),
        _row(match_id=3, days_offset=14, goals=9),  # extreme - would distort game 2 if leaked
    ]
    features = GoalFeatureBuilder().build(rows)
    game2 = features[1]
    assert game2.features["goals_last3_avg"] == 0
    assert game2.games_of_history == 1


def test_appending_a_future_game_does_not_change_earlier_predictions():
    rows_a = [_row(match_id=1, days_offset=0, goals=1), _row(match_id=2, days_offset=7, goals=2)]
    rows_b = rows_a + [_row(match_id=3, days_offset=14, goals=8)]
    features_a = GoalFeatureBuilder().build(rows_a)
    features_b = GoalFeatureBuilder().build(rows_b)
    assert features_a[0].features == features_b[0].features
    assert features_a[1].features == features_b[1].features


def test_goal_history_carries_across_a_mid_career_trade():
    rows = [
        _row(match_id=1, team_id=10, opponent_team_id=20, days_offset=0, goals=1),
        _row(match_id=2, team_id=10, opponent_team_id=30, days_offset=7, goals=3),
        _row(match_id=3, team_id=99, opponent_team_id=20, days_offset=14, goals=0),  # traded
    ]
    features = GoalFeatureBuilder().build(rows)
    game3 = features[2]
    assert game3.games_of_history == 2
    assert game3.features["goals_last3_avg"] == 2  # avg(1, 3), regardless of team change
    assert game3.team_id == 99


def test_season_to_date_average_resets_each_season():
    rows = [
        _row(match_id=1, season_year=2020, days_offset=0, goals=1),
        _row(match_id=2, season_year=2020, days_offset=7, goals=3),
        _row(match_id=3, season_year=2021, days_offset=365, goals=2),
    ]
    features = GoalFeatureBuilder().build(rows)
    game3 = features[2]
    assert game3.features["goals_season_avg"] is None  # no PRIOR 2021 games
    assert game3.features["goals_career_avg"] == 2  # career spans both seasons


def test_season_scale_factor_only_affects_history_never_the_rows_own_target():
    rows = [
        _row(match_id=1, season_year=2020, days_offset=0, goals=1),
        _row(match_id=2, season_year=2021, days_offset=365, goals=0),
    ]
    unadjusted = GoalFeatureBuilder().build(rows)
    scaled = GoalFeatureBuilder(season_scale_factors={2020: 2.0}).build(rows)
    assert unadjusted[0].goals == scaled[0].goals == 1  # actual target never scaled
    assert unadjusted[1].features["goals_last3_avg"] == 1
    assert scaled[1].features["goals_last3_avg"] == 2  # 1 * 2.0


def test_conversion_rate_computed_from_prior_goals_and_behinds():
    rows = [
        _row(match_id=1, days_offset=0, goals=2, behinds=2),  # 2/(2+2) = 0.5
        _row(match_id=2, days_offset=7, goals=99, behinds=99),  # would distort if leaked
    ]
    features = GoalFeatureBuilder().build(rows)
    game2 = features[1]
    assert game2.features["conversion_rate_career"] == 0.5


def test_zero_and_rate_thresholds_computed_from_prior_games_only():
    rows = [
        _row(match_id=1, days_offset=0, goals=0),
        _row(match_id=2, days_offset=7, goals=2),
        _row(match_id=3, days_offset=14, goals=1),
    ]
    features = GoalFeatureBuilder().build(rows)
    game3 = features[2]
    # prior games: [0, 2] -> zero rate = 0.5, 1+ rate = 0.5, 2+ rate = 0.5
    assert game3.features["zero_goal_rate_last10"] == 0.5
    assert game3.features["rate_1plus_last10"] == 0.5
    assert game3.features["rate_2plus_last10"] == 0.5


def test_team_and_opponent_goal_context_use_prior_team_matches_only():
    team_rows = [
        TeamGoalGameRow(team_id=10, opponent_team_id=20, match_id=1, season_year=2020, scheduled_start=BASE_TIME, goals=10, behinds=8, inside_50s=45),
        TeamGoalGameRow(team_id=20, opponent_team_id=10, match_id=1, season_year=2020, scheduled_start=BASE_TIME, goals=8, behinds=10, inside_50s=40),
        TeamGoalGameRow(team_id=10, opponent_team_id=30, match_id=2, season_year=2020, scheduled_start=BASE_TIME + timedelta(days=7), goals=15, behinds=8, inside_50s=50),
        TeamGoalGameRow(team_id=30, opponent_team_id=10, match_id=2, season_year=2020, scheduled_start=BASE_TIME + timedelta(days=7), goals=5, behinds=5, inside_50s=35),
    ]
    player_rows = [_row(player_id=1, match_id=2, team_id=10, opponent_team_id=30, days_offset=7, goals=1)]
    features = GoalFeatureBuilder().build(player_rows, team_rows)
    row = features[0]
    assert row.features["team_recent_goals_avg"] == 10  # team 10's match-1 total, not match-2's own (15)
    assert row.features["opponent_goals_conceded_avg"] is None  # team 30 has no prior matches


def test_venue_environment_uses_shrinkage_with_little_history():
    rows = [_row(match_id=1, venue_id=5, days_offset=0, goals=1)]
    features = GoalFeatureBuilder().build(rows)
    from app.player_modelling.goal_features import LEAGUE_AVG_GOALS_FALLBACK

    assert features[0].features["venue_goals_env"] == LEAGUE_AVG_GOALS_FALLBACK
