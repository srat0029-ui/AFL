import pytest

from app.modelling.elo import EloConfig, EloEngine


def test_equal_ratings_no_home_advantage_gives_fifty_fifty():
    engine = EloEngine(EloConfig(home_advantage=0.0))
    assert engine.expected_home_win_prob(1500, 1500) == pytest.approx(0.5)


def test_home_advantage_favours_home_team_at_equal_ratings():
    engine = EloEngine(EloConfig(home_advantage=35.0))
    prob = engine.expected_home_win_prob(1500, 1500)
    assert prob > 0.5


def test_higher_rated_team_favoured():
    engine = EloEngine(EloConfig(home_advantage=0.0))
    prob = engine.expected_home_win_prob(1600, 1400)
    assert prob > 0.5


def test_expected_prob_symmetric():
    engine = EloEngine(EloConfig(home_advantage=0.0))
    home_prob = engine.expected_home_win_prob(1550, 1450)
    away_prob = engine.expected_home_win_prob(1450, 1550)
    assert home_prob == pytest.approx(1 - away_prob)


def test_update_increases_winner_rating_and_decreases_loser():
    engine = EloEngine(EloConfig(use_margin_of_victory=False, home_advantage=0.0))
    new_home, new_away = engine.update(1500, 1500, home_score=100, away_score=50)
    assert new_home > 1500
    assert new_away < 1500
    # zero-sum: what the winner gains, the loser loses
    assert (new_home - 1500) == pytest.approx(-(new_away - 1500))


def test_update_favourite_winning_moves_ratings_less_than_upset():
    engine = EloEngine(EloConfig(use_margin_of_victory=False, home_advantage=0.0))

    favourite_home, _ = engine.update(1650, 1350, home_score=100, away_score=99)
    underdog_home, _ = engine.update(1350, 1650, home_score=100, away_score=99)

    assert (favourite_home - 1650) < (underdog_home - 1350)


def test_draw_moves_ratings_toward_each_other_not_apart():
    engine = EloEngine(EloConfig(use_margin_of_victory=False, home_advantage=0.0))
    new_home, new_away = engine.update(1600, 1400, home_score=80, away_score=80)
    # the "favourite" (home) was expected to win, so a draw should cost them rating
    assert new_home < 1600
    assert new_away > 1400


def test_margin_of_victory_bigger_win_moves_rating_more():
    engine = EloEngine(EloConfig(use_margin_of_victory=True, home_advantage=0.0))
    small_win_home, _ = engine.update(1500, 1500, home_score=51, away_score=50)
    big_win_home, _ = engine.update(1500, 1500, home_score=120, away_score=50)

    assert (big_win_home - 1500) > (small_win_home - 1500)


def test_margin_of_victory_disabled_ignores_margin_size():
    engine = EloEngine(EloConfig(use_margin_of_victory=False, home_advantage=0.0))
    small_win_home, _ = engine.update(1500, 1500, home_score=51, away_score=50)
    big_win_home, _ = engine.update(1500, 1500, home_score=120, away_score=50)

    assert small_win_home == pytest.approx(big_win_home)


def test_regress_to_mean_pulls_rating_toward_initial():
    engine = EloEngine(EloConfig(initial_rating=1500.0, season_carryover=0.75))
    regressed = engine.regress_to_mean(1700.0)
    assert 1500.0 < regressed < 1700.0
    assert regressed == pytest.approx(1500.0 + 0.75 * 200.0)


def test_regress_to_mean_no_carryover_resets_fully():
    engine = EloEngine(EloConfig(initial_rating=1500.0, season_carryover=0.0))
    assert engine.regress_to_mean(1800.0) == pytest.approx(1500.0)


def test_regress_to_mean_full_carryover_is_noop():
    engine = EloEngine(EloConfig(initial_rating=1500.0, season_carryover=1.0))
    assert engine.regress_to_mean(1800.0) == pytest.approx(1800.0)
