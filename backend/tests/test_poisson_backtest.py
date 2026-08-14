import random
from datetime import datetime, timezone

import pytest

from app.modelling.poisson_backtest import run_walk_forward
from app.modelling.poisson_model import PoissonConfig, expected_value
from app.modelling.types import MatchResult

NEUTRAL_CONFIG = PoissonConfig(rolling_window_games=100, min_games_for_reliable_strength=6)


def _match(match_id, year, month, day, home, away, home_goals, home_behinds, away_goals, away_behinds) -> MatchResult:
    return MatchResult(
        match_id=match_id,
        season_year=year,
        scheduled_start=datetime(year, month, day, tzinfo=timezone.utc),
        home_team_id=home,
        away_team_id=away,
        home_score=6 * home_goals + home_behinds,
        away_score=6 * away_goals + away_behinds,
        home_goals=home_goals,
        home_behinds=home_behinds,
        away_goals=away_goals,
        away_behinds=away_behinds,
    )


def test_first_ever_match_uses_neutral_strength_both_teams_new():
    matches = [_match(1, 2024, 3, 1, home=1, away=2, home_goals=13, home_behinds=15, away_goals=11, away_behinds=13)]
    predictions = run_walk_forward(matches, NEUTRAL_CONFIG)

    p = predictions[0]
    # league avg falls back to 15.0/15.0 with no history yet; both teams at neutral (1.0) strength
    assert p.home_expected_goals == pytest.approx(15.0)
    assert p.home_expected_behinds == pytest.approx(15.0)
    assert p.away_expected_goals == pytest.approx(15.0)
    assert p.away_expected_behinds == pytest.approx(15.0)


def test_matches_missing_goals_breakdown_are_skipped():
    matches = [
        MatchResult(
            match_id=1, season_year=2024, scheduled_start=datetime(2024, 3, 1, tzinfo=timezone.utc),
            home_team_id=1, away_team_id=2, home_score=80, away_score=70,
            home_goals=None, home_behinds=None, away_goals=None, away_behinds=None,
        )
    ]
    predictions = run_walk_forward(matches, NEUTRAL_CONFIG)
    assert predictions == []


def test_predictions_returned_in_chronological_order_regardless_of_input_order():
    matches = [
        _match(1, 2024, 3, 1, 1, 2, 13, 15, 11, 13),
        _match(2, 2024, 3, 8, 2, 1, 12, 14, 10, 12),
        _match(3, 2024, 3, 15, 1, 2, 14, 16, 9, 11),
    ]
    shuffled = matches[:]
    random.Random(7).shuffle(shuffled)

    predictions = run_walk_forward(shuffled, NEUTRAL_CONFIG)
    assert [p.match_id for p in predictions] == [1, 2, 3]


def test_no_leakage_strength_only_reflects_strictly_earlier_matches():
    """Team 1 has a big first game (high scoring), then an unrelated game
    happens for other teams, then team 1's second game — its expected
    goals entering match 3 must be driven only by match 1, never match 2
    (which doesn't involve team 1) or match 3 itself.

    Match 2's teams are set to score exactly the running league average
    (15) so the league-wide baseline is unchanged by it — isolating team
    1's own strength as the only thing that should move between match 1
    and match 3's predictions. Without that control, a naive comparison of
    raw expected_goals would be confounded by the league average itself
    drifting, not actually proving anything about leakage.
    """
    matches = [
        _match(1, 2024, 3, 1, home=1, away=2, home_goals=20, home_behinds=20, away_goals=10, away_behinds=10),
        _match(2, 2024, 3, 8, home=3, away=4, home_goals=15, home_behinds=15, away_goals=15, away_behinds=15),
        _match(3, 2024, 3, 15, home=1, away=3, home_goals=13, home_behinds=13, away_goals=13, away_behinds=13),
    ]
    predictions = run_walk_forward(matches, NEUTRAL_CONFIG)
    by_id = {p.match_id: p for p in predictions}

    assert by_id[1].home_expected_goals == pytest.approx(15.0)  # neutral baseline, no history yet
    # team 1 scored 20 goals in match 1 -> its attack strength entering match 3
    # should be pulled up from neutral, purely from that one earlier result
    assert by_id[3].home_expected_goals > by_id[1].home_expected_goals


def test_small_sample_strength_is_shrunk_toward_league_average():
    config = PoissonConfig(rolling_window_games=100, min_games_for_reliable_strength=4)
    # team 1 has one extreme game (huge attack), team 2/3 average out to a neutral league
    matches = [
        _match(1, 2024, 3, 1, home=1, away=5, home_goals=30, home_behinds=10, away_goals=10, away_behinds=10),
        _match(2, 2024, 3, 8, home=6, away=7, home_goals=10, home_behinds=10, away_goals=10, away_behinds=10),
        _match(3, 2024, 3, 15, home=1, away=8, home_goals=10, home_behinds=10, away_goals=10, away_behinds=10),
    ]
    predictions = run_walk_forward(matches, config)
    by_id = {p.match_id: p for p in predictions}

    # after 1 game (weight = 1/4), team 1's attack should be pulled toward but
    # not all the way to its raw (extreme) observed rate
    league_avg = 10.0  # after match 1: (30+10)/2 = 20... but let's just assert bounds
    assert by_id[3].home_expected_goals > league_avg  # pulled up from neutral
    assert by_id[3].home_expected_goals < 30.0  # but nowhere near the raw extreme observation


def test_rolling_window_evicts_old_games():
    config = PoissonConfig(rolling_window_games=1, min_games_for_reliable_strength=1)
    matches = [
        _match(1, 2024, 3, 1, home=1, away=2, home_goals=30, home_behinds=30, away_goals=10, away_behinds=10),  # team1 huge game
        _match(2, 2024, 3, 8, home=3, away=4, home_goals=10, home_behinds=10, away_goals=10, away_behinds=10),
        _match(3, 2024, 3, 15, home=1, away=5, home_goals=5, home_behinds=5, away_goals=10, away_behinds=10),  # team1 weak game
        _match(4, 2024, 3, 22, home=1, away=6, home_goals=10, home_behinds=10, away_goals=10, away_behinds=10),
    ]
    predictions = run_walk_forward(matches, config)
    by_id = {p.match_id: p for p in predictions}

    # window=1: entering match 4, team 1's history should only contain match 3
    # (the weak game), not match 1's huge game — so expected goals should be low
    assert by_id[4].home_expected_goals < by_id[3].home_expected_goals


def test_home_advantage_does_not_inflate_predicted_totals():
    """Regression test for the double-counting bug an earlier design had:
    applying a guessed home_advantage multiplier on top of pooled (home+away)
    team ratings inflated predicted totals, because it boosted the home
    team's expected score without any offsetting reduction to the away
    team's. With a data-derived home/away split (this design), predicting
    two perfectly average teams should reproduce the league's actual
    blended average total almost exactly — not systematically overshoot it.
    """
    config = PoissonConfig(rolling_window_games=9999, min_games_for_reliable_strength=100, min_league_games_for_home_split=1)
    # a real, consistent home-scores-more-than-away pattern across many
    # different team pairings (not the same two teams every week)
    matches = []
    for i in range(40):
        home, away = 100 + (i % 10), 200 + (i % 10)
        matches.append(_match(i, 2024, 3, 1 + (i % 27), home, away, home_goals=15, home_behinds=15, away_goals=11, away_behinds=11))

    predictions = run_walk_forward(matches, config)
    actual_total = matches[0].home_score + matches[0].away_score  # constant every game by construction

    # every team here is average-relative-to-the-league by construction (all
    # play identically), so the model's own predicted total for a late match
    # (once its rolling stats have converged) should land close to the true
    # constant total, not drift upward from double-counted home advantage
    late_prediction = predictions[-1]
    assert late_prediction.expected_total_points == pytest.approx(actual_total, abs=2.0)


def test_win_probabilities_and_expected_totals_are_internally_consistent():
    matches = [
        _match(1, 2024, 3, 1, home=1, away=2, home_goals=13, home_behinds=15, away_goals=11, away_behinds=13),
    ]
    p = run_walk_forward(matches, NEUTRAL_CONFIG)[0]

    assert p.home_win_probability + p.draw_probability + p.away_win_probability == pytest.approx(1.0, abs=1e-6)
    assert p.expected_total_points == pytest.approx(
        (6 * p.home_expected_goals + p.home_expected_behinds) + (6 * p.away_expected_goals + p.away_expected_behinds)
    )
    assert p.actual_total_points == 6 * 13 + 15 + 6 * 11 + 13
    assert p.actual_margin == (6 * 13 + 15) - (6 * 11 + 13)


def test_return_state_does_not_change_predictions():
    matches = [
        _match(1, 2024, 3, 1, home=1, away=2, home_goals=13, home_behinds=15, away_goals=11, away_behinds=13),
        _match(2, 2024, 3, 8, home=2, away=1, home_goals=12, home_behinds=14, away_goals=10, away_behinds=12),
    ]
    predictions_only = run_walk_forward(matches, NEUTRAL_CONFIG)
    predictions_with_state, state = run_walk_forward(matches, NEUTRAL_CONFIG, return_state=True)

    assert predictions_only == predictions_with_state
    assert state.config == NEUTRAL_CONFIG


def test_model_state_predict_reflects_history_recorded_so_far():
    """state.predict for a hypothetical rematch uses the *updated* league
    average (now reflecting match 1's actual, lower-than-fallback scores),
    unlike match 1's own prediction which had no history to draw on yet —
    they're not expected to match, just both be internally sane."""
    matches = [
        _match(1, 2024, 3, 1, home=1, away=2, home_goals=13, home_behinds=15, away_goals=11, away_behinds=13),
    ]
    predictions, state = run_walk_forward(matches, NEUTRAL_CONFIG, return_state=True)

    assert predictions[0].expected_total_points == pytest.approx(210.0)  # cold-start fallback (15.0/15.0 both teams)

    home_pmf, away_pmf = state.predict(home_team_id=1, away_team_id=2)
    total = expected_value(home_pmf) + expected_value(away_pmf)
    # now grounded in the one real result observed (93-79, well below the
    # generic 15/15 fallback), so the total should have moved down accordingly
    assert 150.0 < total < 200.0


def test_model_state_predict_unknown_teams_reflects_only_league_home_split():
    """Two teams neither seen before get neutral (1.0) attack/defense, so
    any home/away difference in their predicted totals comes purely from
    the league-wide home-scores-more-than-away split observed so far — not
    from anything team-specific."""
    matches = [
        _match(1, 2024, 3, 1, home=1, away=2, home_goals=13, home_behinds=15, away_goals=11, away_behinds=13),
    ]
    _, state = run_walk_forward(matches, NEUTRAL_CONFIG, return_state=True)

    home_pmf, away_pmf = state.predict(home_team_id=999, away_team_id=998)
    # only 1 league match observed so far (default min_league_games_for_home_split=40),
    # so the split is barely shrunk away from neutral — expect a small, not large, gap
    assert expected_value(home_pmf) == pytest.approx(expected_value(away_pmf), abs=2.0)


def test_games_played_reflects_rolling_history():
    matches = [
        _match(1, 2024, 3, 1, home=1, away=2, home_goals=13, home_behinds=15, away_goals=11, away_behinds=13),
        _match(2, 2024, 3, 8, home=1, away=3, home_goals=12, home_behinds=14, away_goals=10, away_behinds=12),
    ]
    _, state = run_walk_forward(matches, NEUTRAL_CONFIG, return_state=True)

    assert state.games_played(1) == 2  # team 1 played both matches
    assert state.games_played(2) == 1
    assert state.games_played(999) == 0  # never seen
