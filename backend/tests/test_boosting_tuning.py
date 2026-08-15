import random
from datetime import datetime, timedelta, timezone

import pytest

from app.modelling.boosting_tuning import select_best_boosting_config
from app.modelling.features import STATS_FEATURE_NAMES, MatchFeatureRow


def _row(match_id, season_year, outcome, feature_overrides=None) -> MatchFeatureRow:
    features = {name: 0.0 for name in STATS_FEATURE_NAMES}
    if feature_overrides:
        features.update(feature_overrides)
    return MatchFeatureRow(
        match_id=match_id, season_year=season_year,
        scheduled_start=datetime(season_year, 3, 1, tzinfo=timezone.utc) + timedelta(days=match_id),
        home_team_id=1, away_team_id=2, actual_home_outcome=outcome, features=features, has_full_history=True,
    )


def _synthetic_tune_rows(seed=0):
    rng = random.Random(seed)
    rows = []
    match_id = 0
    for year in (2016, 2017, 2018):
        for _ in range(150):
            signal = rng.uniform(-1, 1)
            outcome = 1.0 if rng.random() < (0.5 + 0.3 * signal) else 0.0
            features = {name: rng.uniform(-1, 1) for name in STATS_FEATURE_NAMES}
            features["form_diff_5"] = signal
            rows.append(_row(match_id, year, outcome, feature_overrides=features))
            match_id += 1
    return rows


@pytest.mark.parametrize("library", ["xgboost", "lightgbm"])
def test_select_best_config_returns_valid_hyperparameters(library):
    rows = _synthetic_tune_rows()
    grid = {"max_depth": [2, 3], "learning_rate": [0.1], "n_estimators": [50]}
    config, leaderboard = select_best_boosting_config(library, rows, STATS_FEATURE_NAMES, 2018, grid=grid)
    assert config.library == library
    assert config.max_depth in grid["max_depth"]
    assert len(leaderboard) == len(grid["max_depth"]) * len(grid["learning_rate"]) * len(grid["n_estimators"])


def test_leaderboard_sorted_best_first():
    rows = _synthetic_tune_rows()
    grid = {"max_depth": [2, 3, 4], "learning_rate": [0.03, 0.1], "n_estimators": [50]}
    _config, leaderboard = select_best_boosting_config("xgboost", rows, STATS_FEATURE_NAMES, 2018, grid=grid)
    scores = [row["inner_val_brier"] for row in leaderboard]
    assert scores == sorted(scores)


def test_inner_split_never_uses_validation_year_for_training():
    rows = _synthetic_tune_rows()
    grid = {"max_depth": [2], "learning_rate": [0.1], "n_estimators": [50]}
    _config, leaderboard = select_best_boosting_config("xgboost", rows, STATS_FEATURE_NAMES, 2018, grid=grid)
    n_2016_17 = sum(1 for r in rows if r.season_year < 2018)
    n_2018 = sum(1 for r in rows if r.season_year >= 2018)
    assert leaderboard[0]["n_train"] == n_2016_17
    assert leaderboard[0]["n_val"] == n_2018


def test_selection_is_deterministic():
    rows = _synthetic_tune_rows()
    grid = {"max_depth": [2, 3], "learning_rate": [0.1], "n_estimators": [50]}
    config_1, _ = select_best_boosting_config("xgboost", rows, STATS_FEATURE_NAMES, 2018, grid=grid)
    config_2, _ = select_best_boosting_config("xgboost", rows, STATS_FEATURE_NAMES, 2018, grid=grid)
    assert config_1 == config_2
