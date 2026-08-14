from datetime import datetime, timezone

from app.modelling.baselines import (
    always_home_baseline,
    historical_home_win_rate_baseline,
    simple_form_baseline,
)
from app.modelling.types import MatchResult


def _match(match_id, year, month, day, home, away, home_score, away_score) -> MatchResult:
    return MatchResult(
        match_id=match_id,
        season_year=year,
        scheduled_start=datetime(year, month, day, tzinfo=timezone.utc),
        home_team_id=home,
        away_team_id=away,
        home_score=home_score,
        away_score=away_score,
    )


def test_always_home_baseline_predicts_full_confidence():
    matches = [_match(1, 2024, 3, 1, 1, 2, 100, 50), _match(2, 2024, 3, 8, 2, 1, 60, 90)]
    predictions = always_home_baseline(matches)
    assert all(p.home_win_probability == 1.0 for p in predictions)
    assert predictions[0].actual_home_outcome == 1.0  # home actually won match 1
    assert predictions[1].actual_home_outcome == 0.0  # home actually lost match 2


def test_historical_home_win_rate_starts_at_fifty_percent():
    matches = [_match(1, 2024, 3, 1, 1, 2, 100, 50)]
    predictions = historical_home_win_rate_baseline(matches)
    assert predictions[0].home_win_probability == 0.5


def test_historical_home_win_rate_updates_after_each_match():
    matches = [
        _match(1, 2024, 3, 1, 1, 2, 100, 50),  # home wins
        _match(2, 2024, 3, 8, 3, 4, 90, 40),  # home wins again
        _match(3, 2024, 3, 15, 1, 3, 50, 90),  # home loses
    ]
    predictions = historical_home_win_rate_baseline(matches)
    assert predictions[0].home_win_probability == 0.5
    assert predictions[1].home_win_probability > 0.5  # one home win observed so far
    assert predictions[2].home_win_probability > predictions[1].home_win_probability  # two home wins now


def test_historical_home_win_rate_is_leakage_safe_expanding_average():
    """The probability for match N must depend only on matches 1..N-1, not
    on the full dataset — i.e. it must NOT equal the final overall rate for
    every match (that would mean it peeked at the whole dataset)."""
    matches = [
        _match(1, 2024, 3, 1, 1, 2, 40, 90),  # home loses
        _match(2, 2024, 3, 8, 3, 4, 40, 90),  # home loses
        _match(3, 2024, 3, 15, 1, 3, 100, 10),  # home wins
    ]
    predictions = historical_home_win_rate_baseline(matches)
    final_overall_rate = sum(p.actual_home_outcome for p in predictions) / len(predictions)
    # the first prediction can't already reflect the outcome of matches 2 and 3
    assert predictions[0].home_win_probability != final_overall_rate


def test_simple_form_baseline_defaults_to_neutral_for_unseen_teams():
    matches = [_match(1, 2024, 3, 1, 1, 2, 100, 50)]
    predictions = simple_form_baseline(matches)
    assert predictions[0].home_win_probability == 0.5


def test_simple_form_baseline_favours_team_on_a_winning_streak():
    matches = [
        _match(1, 2024, 3, 1, 1, 2, 100, 40),  # team 1 wins
        _match(2, 2024, 3, 8, 1, 3, 100, 40),  # team 1 wins again
        _match(3, 2024, 3, 15, 1, 4, 80, 70),  # team 1 (in form) at home again
    ]
    predictions = simple_form_baseline(matches)
    assert predictions[2].home_win_probability > 0.5


def test_simple_form_baseline_probabilities_stay_within_clamp_bounds():
    matches = [_match(i, 2024, 3, 1 + i, 1, 2, 150, 10) for i in range(1, 15)]
    predictions = simple_form_baseline(matches)
    assert all(0.05 <= p.home_win_probability <= 0.95 for p in predictions)


def test_baselines_handle_empty_input():
    assert always_home_baseline([]) == []
    assert historical_home_win_rate_baseline([]) == []
    assert simple_form_baseline([]) == []
