import random
from datetime import datetime, timedelta, timezone

import pytest

from app.modelling.boosting import BoostingConfig, fit_boosting_model, predict
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


def _synthetic_rows(n, season_year=2016, seed=0, signal_feature="form_diff_5", strength=0.35):
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        signal = rng.uniform(-1, 1)
        outcome = 1.0 if rng.random() < (0.5 + strength * signal) else 0.0
        features = {name: rng.uniform(-1, 1) for name in STATS_FEATURE_NAMES}
        features[signal_feature] = signal
        rows.append(_row(i, season_year, outcome, feature_overrides=features))
    return rows


@pytest.mark.parametrize("library", ["xgboost", "lightgbm"])
def test_fit_and_predict_produces_valid_probabilities(library):
    rows = _synthetic_rows(200)
    config = BoostingConfig(library=library, feature_names=STATS_FEATURE_NAMES)
    model = fit_boosting_model(rows, config)
    preds = predict(model, rows, STATS_FEATURE_NAMES)
    assert len(preds) == len(rows)
    assert all(0.0 <= p.home_win_probability <= 1.0 for p in preds)


@pytest.mark.parametrize("library", ["xgboost", "lightgbm"])
def test_model_learns_the_real_signal(library):
    rows = _synthetic_rows(400, seed=1)
    config = BoostingConfig(library=library, feature_names=STATS_FEATURE_NAMES, max_depth=2, n_estimators=50)
    model = fit_boosting_model(rows, config)

    strong_home = _row(9001, 2016, 1.0, {**{n: 0.0 for n in STATS_FEATURE_NAMES}, "form_diff_5": 5.0})
    strong_away = _row(9002, 2016, 1.0, {**{n: 0.0 for n in STATS_FEATURE_NAMES}, "form_diff_5": -5.0})
    preds = predict(model, [strong_home, strong_away], STATS_FEATURE_NAMES)
    assert preds[0].home_win_probability > preds[1].home_win_probability


@pytest.mark.parametrize("library", ["xgboost", "lightgbm"])
def test_missing_features_do_not_crash_native_handling(library):
    """Trees handle NaN natively — no imputer required — so a row with
    missing rolling-history features should still predict without error."""
    rows = _synthetic_rows(150)
    config = BoostingConfig(library=library, feature_names=STATS_FEATURE_NAMES)
    model = fit_boosting_model(rows, config)

    missing_row = _row(9999, 2016, 1.0, {"form_diff_5": None, "clearance_differential_diff": None})
    preds = predict(model, [missing_row], STATS_FEATURE_NAMES)
    assert 0.0 <= preds[0].home_win_probability <= 1.0


@pytest.mark.parametrize("library", ["xgboost", "lightgbm"])
def test_draws_excluded_from_fitting_but_not_prediction(library):
    rows = _synthetic_rows(100) + [_row(999, 2016, 0.5)]
    config = BoostingConfig(library=library, feature_names=STATS_FEATURE_NAMES)
    model = fit_boosting_model(rows, config)  # must not raise
    preds = predict(model, rows, STATS_FEATURE_NAMES)
    draw_pred = next(p for p in preds if p.match_id == 999)
    assert 0.0 <= draw_pred.home_win_probability <= 1.0


@pytest.mark.parametrize("library", ["xgboost", "lightgbm"])
def test_fitting_is_deterministic_given_same_random_state(library):
    rows = _synthetic_rows(150, seed=2)
    config = BoostingConfig(library=library, feature_names=STATS_FEATURE_NAMES, random_state=7)
    model_1 = fit_boosting_model(rows, config)
    model_2 = fit_boosting_model(rows, config)
    preds_1 = predict(model_1, rows, STATS_FEATURE_NAMES)
    preds_2 = predict(model_2, rows, STATS_FEATURE_NAMES)
    assert [p.home_win_probability for p in preds_1] == [p.home_win_probability for p in preds_2]


def test_predict_on_empty_rows_returns_empty_list():
    rows = _synthetic_rows(50)
    model = fit_boosting_model(rows, BoostingConfig(library="xgboost", feature_names=STATS_FEATURE_NAMES))
    assert predict(model, [], STATS_FEATURE_NAMES) == []


def test_unknown_library_raises():
    with pytest.raises(ValueError):
        fit_boosting_model(_synthetic_rows(50), BoostingConfig(library="not_a_library", feature_names=STATS_FEATURE_NAMES))
