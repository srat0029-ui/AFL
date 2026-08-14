from datetime import datetime, timezone

from app.modelling.elo_backtest import MatchResult
from app.modelling.elo_tuning import select_best_config


def _home_always_wins_matches(n: int) -> list[MatchResult]:
    """Alternates which of two teams is "home" each week, and the home team
    always wins by a modest margin — a dataset that should clearly reward
    configs with a real home_advantage over ones with none."""
    matches = []
    for i in range(n):
        home, away = (1, 2) if i % 2 == 0 else (2, 1)
        matches.append(
            MatchResult(
                match_id=i,
                season_year=2024,
                scheduled_start=datetime(2024, 1, 1, tzinfo=timezone.utc).replace(day=1 + (i % 27)),
                home_team_id=home,
                away_team_id=away,
                home_score=80,
                away_score=70,
            )
        )
    return matches


def test_select_best_config_prefers_home_advantage_when_data_supports_it():
    matches = _home_always_wins_matches(30)
    grid = {
        "k_factor": [20.0],
        "home_advantage": [0.0, 60.0],
        "use_margin_of_victory": [False],
        "season_carryover": [1.0],
    }

    best_config, leaderboard = select_best_config(matches, grid)

    assert best_config.home_advantage == 60.0
    assert len(leaderboard) == 2
    assert leaderboard[0]["tune_brier"] <= leaderboard[1]["tune_brier"]


def test_leaderboard_is_sorted_best_first():
    matches = _home_always_wins_matches(20)
    grid = {
        "k_factor": [10.0, 20.0, 40.0],
        "home_advantage": [0.0, 30.0],
        "use_margin_of_victory": [True],
        "season_carryover": [0.75],
    }

    _, leaderboard = select_best_config(matches, grid)

    scores = [row["tune_brier"] for row in leaderboard]
    assert scores == sorted(scores)
    assert len(leaderboard) == 6  # 3 * 2 * 1 * 1
