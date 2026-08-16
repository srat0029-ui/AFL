"""Tests for goal_backtest.py's split/common-evaluation-set/determinism
guarantees — mirrors test_disposal_backtest.py's pattern exactly.
"""

import random
from datetime import datetime, timedelta, timezone

from app.player_modelling.goal_backtest import (
    EVALUATION_START_YEAR,
    build_goal_dataset_from_rows,
    run_goal_baselines,
    run_goal_candidate_models,
)
from app.player_modelling.goal_data import PlayerGoalGameRow

BASE_2018 = datetime(2018, 4, 1, tzinfo=timezone.utc)
BASE_2019 = datetime(2019, 4, 1, tzinfo=timezone.utc)

# A small, well-conditioned feature subset for statsmodels-backed models
# (negative_binomial, hurdle) on tiny synthetic fixtures - the full
# PLAYER_FEATURE_NAMES set is mostly all-None here (no team_rows/
# team_context passed), which median-imputes to constant columns and
# produces a singular Hessian for statsmodels' NB MLE. gbm_xgboost has no
# such issue (tree models don't need a well-conditioned design matrix), so
# it's tested against the full feature set elsewhere; this subset exists
# only to keep the statsmodels-based fits numerically stable in tests.
_SAFE_FEATURES = ("goals_last5_avg", "goals_career_avg")


def _row(player_id, match_id, season_year, when, goals):
    return PlayerGoalGameRow(
        player_id=player_id, match_id=match_id, team_id=10, opponent_team_id=20, season_year=season_year,
        round_number=1, is_final=False, is_home=True, venue_id=1, scheduled_start=when, goals=goals, behinds=1,
        disposals=15, kicks=8, marks=4, handballs=7, tackles=3, contested_possessions=5,
        uncontested_possessions=6, inside_50s=2, marks_inside_50=1, goal_assists=0, time_on_ground_pct=80,
        subbed_on=False, subbed_off=False,
    )


def _synthetic_rows(n_players=8, n_games_per_player=6):
    rows = []
    match_id = 1
    for p in range(1, n_players + 1):
        for g in range(n_games_per_player):
            season_year = 2018 if g < 3 else 2019
            base = BASE_2018 if season_year == 2018 else BASE_2019
            rows.append(_row(p, match_id, season_year, base + timedelta(days=7 * g), goals=(p + g) % 4))
            match_id += 1
    return rows


def _statsmodels_synthetic_rows(n_players=40, n_games_per_player=16):
    """A larger, randomised (not a deterministic modular pattern) fixture -
    statsmodels' NB maximum-likelihood fit needs enough sample variation to
    converge to a non-singular Hessian; the small, perfectly-patterned
    fixture above is fine for structural tests but too degenerate for an
    actual MLE fit."""
    rng = random.Random(42)
    rows = []
    match_id = 1
    for p in range(1, n_players + 1):
        for g in range(n_games_per_player):
            season_year = 2018 if g < n_games_per_player // 2 else 2019
            base = BASE_2018 if season_year == 2018 else BASE_2019
            rows.append(_row(p, match_id, season_year, base + timedelta(days=7 * g), goals=rng.choices([0, 1, 2, 3], weights=[6, 3, 1, 1])[0]))
            match_id += 1
    return rows


def test_tune_eval_split_uses_evaluation_start_year_boundary():
    split = build_goal_dataset_from_rows(_synthetic_rows(), team_rows=[], team_context={})
    assert all(r.season_year < EVALUATION_START_YEAR for r in split.tune_rows)
    assert all(r.season_year >= EVALUATION_START_YEAR for r in split.eval_rows)


def test_common_evaluation_set_identical_across_baselines_and_models():
    split = build_goal_dataset_from_rows(_statsmodels_synthetic_rows(), team_rows=[], team_context={})
    baseline_preds = run_goal_baselines(split)
    model_preds = run_goal_candidate_models(split, feature_names=_SAFE_FEATURES, model_names=("negative_binomial", "hurdle"))

    expected_keys = {(r.player_id, r.match_id) for r in split.eval_rows}
    for name, preds in {**baseline_preds, **model_preds}.items():
        keys = {(p.player_id, p.match_id) for p in preds}
        assert keys == expected_keys, f"{name} evaluated a different row set"


def test_backtest_is_deterministic_across_repeated_runs():
    rows = _statsmodels_synthetic_rows()
    split_a = build_goal_dataset_from_rows(rows, team_rows=[], team_context={})
    split_b = build_goal_dataset_from_rows(rows, team_rows=[], team_context={})

    preds_a = run_goal_candidate_models(split_a, feature_names=_SAFE_FEATURES, model_names=("negative_binomial", "hurdle", "gbm_xgboost"))
    preds_b = run_goal_candidate_models(split_b, feature_names=_SAFE_FEATURES, model_names=("negative_binomial", "hurdle", "gbm_xgboost"))

    for name in preds_a:
        means_a = [p.predicted_mean for p in preds_a[name]]
        means_b = [p.predicted_mean for p in preds_b[name]]
        assert means_a == means_b, f"{name} predictions were not deterministic"


def test_hurdle_predictions_carry_p_score_and_mu_scored_not_nb_alpha():
    split = build_goal_dataset_from_rows(_statsmodels_synthetic_rows(), team_rows=[], team_context={})
    hurdle_preds = run_goal_candidate_models(split, feature_names=_SAFE_FEATURES, model_names=("hurdle",))["hurdle"]
    for p in hurdle_preds:
        assert p.distribution_kind == "hurdle"
        assert p.p_score is not None
        assert p.mu_scored is not None
        assert p.alpha_scored is not None
        assert 0.0 <= p.p_score <= 1.0


def test_nb_predictions_carry_nb_alpha_not_hurdle_params():
    split = build_goal_dataset_from_rows(_statsmodels_synthetic_rows(), team_rows=[], team_context={})
    nb_preds = run_goal_candidate_models(split, feature_names=_SAFE_FEATURES, model_names=("negative_binomial",))["negative_binomial"]
    for p in nb_preds:
        assert p.distribution_kind == "nb"
        assert p.nb_alpha is not None
        assert p.p_score is None
        assert p.mu_scored is None


def test_eval_rows_disjoint_from_tune_rows_by_match_id():
    rows = _synthetic_rows()
    split = build_goal_dataset_from_rows(rows, team_rows=[], team_context={})
    tune_match_ids = {r.match_id for r in split.tune_rows}
    eval_match_ids = {r.match_id for r in split.eval_rows}
    assert tune_match_ids.isdisjoint(eval_match_ids)
