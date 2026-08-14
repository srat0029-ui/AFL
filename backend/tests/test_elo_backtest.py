import random
from datetime import datetime, timezone

import pytest

from app.modelling.elo import EloConfig
from app.modelling.elo_backtest import MatchResult, current_ratings, run_walk_forward

CONFIG = EloConfig(k_factor=32.0, home_advantage=0.0, initial_rating=1500.0, use_margin_of_victory=False)


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


def test_new_team_starts_at_initial_rating():
    matches = [_match(1, 2024, 3, 1, home=1, away=2, home_score=100, away_score=50)]
    predictions = run_walk_forward(matches, CONFIG)

    assert predictions[0].home_rating_before == CONFIG.initial_rating
    assert predictions[0].away_rating_before == CONFIG.initial_rating


def test_predictions_returned_in_chronological_order_regardless_of_input_order():
    matches = [
        _match(1, 2024, 3, 1, home=1, away=2, home_score=100, away_score=50),
        _match(2, 2024, 3, 8, home=2, away=1, home_score=60, away_score=70),
        _match(3, 2024, 3, 15, home=1, away=2, home_score=80, away_score=90),
    ]
    shuffled = matches[:]
    random.Random(42).shuffle(shuffled)

    predictions = run_walk_forward(shuffled, CONFIG)

    assert [p.match_id for p in predictions] == [1, 2, 3]


def test_no_leakage_ratings_only_reflect_strictly_earlier_matches():
    """Team 1's rating entering match 3 must equal exactly what it was
    after match 1 (its only prior game) — never influenced by match 2's
    result, and never by match 3 itself."""
    matches = [
        _match(1, 2024, 3, 1, home=1, away=2, home_score=100, away_score=50),  # team 1 beats team 2 big
        _match(2, 2024, 3, 8, home=3, away=4, home_score=60, away_score=70),  # unrelated game
        _match(3, 2024, 3, 15, home=1, away=3, home_score=80, away_score=90),  # team 1's second game
    ]
    predictions = run_walk_forward(matches, CONFIG)
    by_id = {p.match_id: p for p in predictions}

    assert by_id[3].home_rating_before == pytest.approx(by_id[1].home_rating_after)
    # and team 1's rating entering match 3 must differ from its initial rating
    # (proves match 1 *did* feed forward), while being independent of match 2/3
    assert by_id[3].home_rating_before != CONFIG.initial_rating


def test_ratings_chain_correctly_across_repeated_matchups():
    matches = [
        _match(1, 2024, 3, 1, home=1, away=2, home_score=100, away_score=50),
        _match(2, 2024, 3, 8, home=2, away=1, home_score=90, away_score=60),
        _match(3, 2024, 3, 15, home=1, away=2, home_score=70, away_score=71),
    ]
    predictions = run_walk_forward(matches, CONFIG)
    by_id = {p.match_id: p for p in predictions}

    # team 2's rating entering match 2 = team 2's rating after match 1
    assert by_id[2].home_rating_before == pytest.approx(by_id[1].away_rating_after)
    # team 1's rating entering match 3 = team 1's rating after match 2
    assert by_id[3].home_rating_before == pytest.approx(by_id[2].away_rating_after)


def test_season_boundary_applies_carryover_regression():
    config = EloConfig(k_factor=32.0, home_advantage=0.0, initial_rating=1500.0, season_carryover=0.5)
    matches = [
        _match(1, 2024, 3, 1, home=1, away=2, home_score=150, away_score=50),  # big win, team 1 rating rises a lot
        _match(2, 2025, 3, 1, home=1, away=3, home_score=80, away_score=79),  # team 1's first 2025 game
    ]
    predictions = run_walk_forward(matches, config)
    by_id = {p.match_id: p for p in predictions}

    rating_after_2024 = by_id[1].home_rating_after
    rating_entering_2025 = by_id[2].home_rating_before

    assert rating_after_2024 > 1500.0  # sanity: team 1 did in fact gain rating
    assert rating_entering_2025 == pytest.approx(1500.0 + 0.5 * (rating_after_2024 - 1500.0))
    assert 1500.0 < rating_entering_2025 < rating_after_2024


def test_no_regression_applied_within_same_season():
    config = EloConfig(k_factor=32.0, home_advantage=0.0, season_carryover=0.0)  # would fully reset if (mis)applied
    matches = [
        _match(1, 2024, 3, 1, home=1, away=2, home_score=100, away_score=50),
        _match(2, 2024, 3, 8, home=1, away=3, home_score=80, away_score=79),
    ]
    predictions = run_walk_forward(matches, config)
    by_id = {p.match_id: p for p in predictions}

    # same season as match 1 -> no regression should fire, even though carryover=0.0
    assert by_id[2].home_rating_before == pytest.approx(by_id[1].home_rating_after)


def test_current_ratings_reflects_latest_rating_per_team():
    matches = [
        _match(1, 2024, 3, 1, home=1, away=2, home_score=100, away_score=50),
        _match(2, 2024, 3, 8, home=1, away=3, home_score=60, away_score=90),
    ]
    predictions = run_walk_forward(matches, CONFIG)
    ratings = current_ratings(predictions)

    team1_rating, team1_season = ratings[1]
    assert team1_rating == pytest.approx(predictions[-1].home_rating_after)
    assert team1_season == 2024
