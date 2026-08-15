import random
import warnings
from datetime import datetime, timedelta, timezone

from app.modelling.features import STATS_FEATURE_NAMES, MatchFeatureRow
from app.modelling.logistic_tuning import select_best_C

warnings.filterwarnings("ignore", category=FutureWarning)


def _row(match_id, season_year, outcome, feature_overrides=None, has_full_history=True) -> MatchFeatureRow:
    features = {name: 0.0 for name in STATS_FEATURE_NAMES}
    if feature_overrides:
        features.update(feature_overrides)
    return MatchFeatureRow(
        match_id=match_id, season_year=season_year,
        scheduled_start=datetime(season_year, 3, 1, tzinfo=timezone.utc) + timedelta(days=match_id),
        home_team_id=1, away_team_id=2, actual_home_outcome=outcome,
        features=features, has_full_history=has_full_history,
    )


def _synthetic_tune_rows(seed: int = 0) -> list[MatchFeatureRow]:
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


def test_select_best_C_returns_a_value_from_the_grid():
    rows = _synthetic_tune_rows()
    grid = [0.1, 1.0, 10.0]
    best_C, leaderboard = select_best_C(rows, STATS_FEATURE_NAMES, inner_validation_start_year=2018, C_grid=grid)
    assert best_C in grid
    assert len(leaderboard) == len(grid)


def test_leaderboard_sorted_best_first():
    rows = _synthetic_tune_rows()
    _, leaderboard = select_best_C(rows, STATS_FEATURE_NAMES, inner_validation_start_year=2018)
    scores = [row["inner_val_brier"] for row in leaderboard]
    assert scores == sorted(scores)


def test_inner_split_never_uses_validation_year_for_training():
    rows = _synthetic_tune_rows()
    _, leaderboard = select_best_C(rows, STATS_FEATURE_NAMES, inner_validation_start_year=2018)
    # n_train should equal count of 2016+2017 rows; n_val should equal 2018 rows
    n_2016_17 = sum(1 for r in rows if r.season_year < 2018)
    n_2018 = sum(1 for r in rows if r.season_year >= 2018)
    assert leaderboard[0]["n_train"] == n_2016_17
    assert leaderboard[0]["n_val"] == n_2018


def test_selection_is_deterministic():
    rows = _synthetic_tune_rows()
    best_C_1, _ = select_best_C(rows, STATS_FEATURE_NAMES, inner_validation_start_year=2018)
    best_C_2, _ = select_best_C(rows, STATS_FEATURE_NAMES, inner_validation_start_year=2018)
    assert best_C_1 == best_C_2


def test_rows_without_full_history_excluded_from_inner_split():
    rows = _synthetic_tune_rows()
    incomplete_row = _row(9999, 2016, 1.0, has_full_history=False)
    _, leaderboard_with = select_best_C(rows + [incomplete_row], STATS_FEATURE_NAMES, inner_validation_start_year=2018)
    _, leaderboard_without = select_best_C(rows, STATS_FEATURE_NAMES, inner_validation_start_year=2018)
    assert leaderboard_with[0]["n_train"] == leaderboard_without[0]["n_train"]
