from datetime import datetime, timezone

from app.modelling.poisson_tuning import select_best_config
from app.modelling.types import MatchResult


def _match(match_id, home, away, home_goals, home_behinds, away_goals, away_behinds) -> MatchResult:
    return MatchResult(
        match_id=match_id,
        season_year=2024,
        scheduled_start=datetime(2024, 1, 1, tzinfo=timezone.utc).replace(day=1 + (match_id % 27)),
        home_team_id=home,
        away_team_id=away,
        home_score=6 * home_goals + home_behinds,
        away_score=6 * away_goals + away_behinds,
        home_goals=home_goals,
        home_behinds=home_behinds,
        away_goals=away_goals,
        away_behinds=away_behinds,
    )


def _team_1_recently_improved_matches() -> list[MatchResult]:
    """Team 1 was weak for its first 10 games, then became strong for the
    next 10 — a dataset that should clearly reward a short rolling window
    (which tracks the improvement) over a long one (which stays anchored to
    the stale early-season form).

    Team 1 rotates through different opponents (not the same one every
    week) — otherwise a short window creates a pathological feedback loop:
    that single opponent's defense rating and team 1's attack rating get
    estimated from the exact same small, correlated set of games, which
    compounds into runaway overshoot in a way real 18-team AFL data (where
    most opponents are faced once or twice a season) wouldn't. Rotating
    opponents here keeps each opponent's own rating near-neutral (one data
    point each, heavily shrunk), isolating the thing actually being tested:
    whether the window tracks *team 1's* form shift.
    """
    matches = []
    match_id = 0
    for opponent in [2, 3, 4, 5, 6]:
        matches.append(_match(match_id, home=1, away=opponent, home_goals=8, home_behinds=8, away_goals=15, away_behinds=15))
        match_id += 1
    for opponent in [7, 8, 9, 10, 11]:
        matches.append(_match(match_id, home=1, away=opponent, home_goals=20, home_behinds=20, away_goals=10, away_behinds=10))
        match_id += 1
    # a final probe game whose result is being predicted (not itself informative either way)
    matches.append(_match(match_id, home=1, away=12, home_goals=20, home_behinds=20, away_goals=10, away_behinds=10))
    return matches


def test_select_best_config_prefers_short_window_when_form_has_shifted():
    matches = _team_1_recently_improved_matches()
    grid = {
        "rolling_window_games": [4, 9999],  # short (tracks the shift) vs effectively-expanding (stays stale)
        "min_games_for_reliable_strength": [4],
        "min_league_games_for_home_split": [9999],  # keep home/away split neutral, isolate the window's effect
    }

    best_config, leaderboard = select_best_config(matches, grid)

    assert best_config.rolling_window_games == 4
    assert len(leaderboard) == 2
    assert leaderboard[0]["tune_total_points_mae"] <= leaderboard[1]["tune_total_points_mae"]


def test_leaderboard_sorted_best_first():
    matches = _team_1_recently_improved_matches()
    grid = {
        "rolling_window_games": [4, 22, 9999],
        "min_games_for_reliable_strength": [4, 8],
        "min_league_games_for_home_split": [9999],
    }

    _, leaderboard = select_best_config(matches, grid)

    scores = [row["tune_total_points_mae"] for row in leaderboard]
    assert scores == sorted(scores)
    assert len(leaderboard) == 6
