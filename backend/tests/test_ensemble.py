import pytest

from app.modelling.ensemble import blend, select_ensemble_weight


def test_blend_pure_elo_at_weight_zero():
    elo = [0.6, 0.4, 0.7]
    boosting = [0.9, 0.1, 0.2]
    assert blend(elo, boosting, 0.0) == elo


def test_blend_pure_boosting_at_weight_one():
    elo = [0.6, 0.4, 0.7]
    boosting = [0.9, 0.1, 0.2]
    assert blend(elo, boosting, 1.0) == boosting


def test_blend_midpoint():
    elo = [0.6]
    boosting = [0.8]
    assert blend(elo, boosting, 0.5) == [pytest.approx(0.7)]


def test_select_weight_prefers_the_better_model():
    # boosting is much better than elo here -> weight should favour boosting
    outcomes = [1.0, 0.0, 1.0, 0.0, 1.0] * 10
    elo = [0.5, 0.5, 0.5, 0.5, 0.5] * 10  # uninformative
    boosting = [0.95, 0.05, 0.95, 0.05, 0.95] * 10  # excellent

    weight, leaderboard = select_ensemble_weight(elo, boosting, outcomes)

    assert weight > 0.5
    assert leaderboard == sorted(leaderboard, key=lambda r: r.inner_val_brier)


def test_select_weight_prefers_elo_when_elo_is_better():
    outcomes = [1.0, 0.0, 1.0, 0.0, 1.0] * 10
    elo = [0.95, 0.05, 0.95, 0.05, 0.95] * 10
    boosting = [0.5, 0.5, 0.5, 0.5, 0.5] * 10

    weight, _ = select_ensemble_weight(elo, boosting, outcomes)

    assert weight < 0.5


def test_selection_deterministic():
    outcomes = [1.0, 0.0, 1.0, 0.0] * 10
    elo = [0.6, 0.4, 0.6, 0.4] * 10
    boosting = [0.55, 0.45, 0.65, 0.35] * 10
    weight_1, board_1 = select_ensemble_weight(elo, boosting, outcomes)
    weight_2, board_2 = select_ensemble_weight(elo, boosting, outcomes)
    assert weight_1 == weight_2
    assert board_1 == board_2


def test_custom_weight_grid_respected():
    outcomes = [1.0, 0.0] * 10
    elo = [0.6, 0.4] * 10
    boosting = [0.7, 0.3] * 10
    weight, leaderboard = select_ensemble_weight(elo, boosting, outcomes, weight_grid=[0.25, 0.75])
    assert weight in (0.25, 0.75)
    assert len(leaderboard) == 2
