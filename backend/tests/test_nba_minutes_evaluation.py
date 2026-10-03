"""Expected Minutes Model V1 - chronological evaluation, preprocessing fitted
on training data only, metrics, baselines and the uncertainty table."""

import numpy as np
import pandas as pd
import pytest

from app.nba.minutes import evaluation as ev
from app.nba.minutes.features import ALL_FEATURES
from app.nba.minutes.metrics import point_metrics
from app.nba.minutes.models import BASELINES, CANDIDATES, DropEmptyColumns, baseline_predictions, fit_predict
from app.nba.minutes.residuals import apply_uncertainty, fit_uncertainty_table, interval_coverage


def _synthetic(seasons=range(2018, 2024), per_season=400, seed=0) -> pd.DataFrame:
    """Rows shaped like the real dataset: minutes driven by a recent-minutes
    feature plus noise."""
    rng = np.random.default_rng(seed)
    frames = []
    for s in seasons:
        n = per_season
        base = rng.uniform(5, 38, n)
        f = pd.DataFrame({c: rng.normal(0, 1, n) for c in ALL_FEATURES})
        for c in ("min_last1", "min_mean3", "min_mean5", "min_mean10", "min_ewm", "season_mean", "min_median10"):
            f[c] = base + rng.normal(0, 2, n)
        f["season_start_year"] = s
        f["season_type"] = "regular"
        f["minutes"] = np.clip(base + rng.normal(0, 4, n), 0, 48)
        f["minutes_or_zero"] = f["minutes"]
        f["played"] = True
        f["eligible"] = True
        frames.append(f)
    return pd.concat(frames, ignore_index=True)


def test_point_metrics_known_values():
    m = point_metrics([10, 20, 30, 40], [12, 20, 25, 47])
    assert m["n"] == 4
    assert m["mae"] == pytest.approx((2 + 0 + 5 + 7) / 4)
    assert m["bias"] == pytest.approx((-2 + 0 + 5 - 7) / 4)
    assert m["rmse"] == pytest.approx(np.sqrt((4 + 0 + 25 + 49) / 4))
    assert m["median_ae"] == pytest.approx(3.5)
    assert m["within_2"] == 0.5 and m["within_4"] == 0.5 and m["within_6"] == 0.75


def test_point_metrics_refuses_missing_values():
    with pytest.raises(ValueError):
        point_metrics([1.0, np.nan], [1.0, 2.0])


def test_baselines_cover_every_eligible_row_and_season_mean_falls_back():
    f = pd.DataFrame({"min_last1": [30.0], "min_mean3": [29.0], "min_mean5": [28.0], "min_mean10": [27.0], "season_mean": [np.nan], "min_ewm": [28.5]})
    b = baseline_predictions(f)
    assert list(b.columns) == list(BASELINES)
    assert b.loc[0, "season_to_date_mean"] == 27.0
    with pytest.raises(ValueError):
        baseline_predictions(f.assign(min_last1=np.nan))


def test_walk_forward_trains_only_on_earlier_seasons():
    data = _synthetic()
    preds, meta = ev.walk_forward(data, ["ridge"], folds=(2021, 2022, 2023), log=lambda *_: None)
    for season, fold in meta.items():
        assert max(fold["train_seasons"]) == season - 1
        assert all(s < season for s in fold["train_seasons"])
    assert set(preds["season_start_year"]) == {2021, 2022, 2023}


def test_inner_tuning_never_sees_the_test_season(monkeypatch):
    seen = []
    real = ev.tune

    def spy(name, rows, train_seasons, val_season, features):
        seen.append((tuple(train_seasons), val_season))
        return real(name, rows, train_seasons, val_season, features)

    monkeypatch.setattr(ev, "tune", spy)
    ev.walk_forward(_synthetic(), ["ridge"], folds=(2022, 2023), log=lambda *_: None)
    for (train, val), test_season in zip(seen, (2022, 2023)):
        assert val == test_season - 1 and max(train) < val


def test_preprocessing_is_fitted_on_training_rows_only():
    data = _synthetic()
    train = data[data["season_start_year"] < 2023]
    test = data[data["season_start_year"] == 2023].copy()
    model, _ = fit_predict(CANDIDATES["ridge"], {"alpha": 10.0}, train, test, ALL_FEATURES)
    imputer = model.estimator.named_steps["simpleimputer"]
    scaler = model.estimator.named_steps["standardscaler"]
    np.testing.assert_allclose(imputer.statistics_, train[ALL_FEATURES].median().to_numpy())
    # Wildly different test rows cannot move any learned parameter.
    test[ALL_FEATURES] = test[ALL_FEATURES] * 1000 + 5000
    model2, _ = fit_predict(CANDIDATES["ridge"], {"alpha": 10.0}, train, test, ALL_FEATURES)
    np.testing.assert_allclose(model2.estimator.named_steps["standardscaler"].mean_, scaler.mean_)


def test_test_rows_do_not_influence_each_others_predictions():
    data = _synthetic()
    train, test = data[data["season_start_year"] < 2023], data[data["season_start_year"] == 2023].copy()
    _, p1 = fit_predict(CANDIDATES["hgb"], {"max_iter": 50}, train, test, ALL_FEATURES)
    test2 = test.copy()
    test2.iloc[1:, test2.columns.get_indexer(ALL_FEATURES)] = 0.0
    _, p2 = fit_predict(CANDIDATES["hgb"], {"max_iter": 50}, train, test2, ALL_FEATURES)
    assert p1[0] == p2[0]


def test_columns_empty_in_training_are_dropped_by_a_training_only_decision():
    X = np.array([[1.0, np.nan], [2.0, np.nan], [3.0, np.nan]])
    m = DropEmptyColumns(CANDIDATES["ridge"].build({"alpha": 1.0})).fit(X, np.array([1.0, 2.0, 3.0]))
    assert m.kept_.tolist() == [True, False]
    assert np.isfinite(m.predict(np.array([[4.0, 99.0]]))).all()


def test_model_beats_noise_on_learnable_synthetic_data():
    data = _synthetic(per_season=600)
    preds, _ = ev.walk_forward(data, ["ridge"], folds=(2023,), log=lambda *_: None)
    s = ev.summarize(preds, ["ridge", "last_game"], [2023])
    assert s["ridge"]["mae"] < s["last_game"]["mae"]


def test_selection_prefers_simpler_model_within_tolerance():
    from app.nba.minutes.experiment import _select

    summary = {"ridge": {"mae": 5.01}, "hgb": {"mae": 5.00}, "lgbm": {"mae": 4.995}, "random_forest": {"mae": 5.2}}
    chosen, ranking = _select(summary)
    assert chosen == "ridge"
    assert ranking[0]["model"] == "lgbm"
    chosen2, _ = _select({**summary, "hgb": {"mae": 4.90}})
    assert chosen2 == "hgb"


def test_uncertainty_table_from_residuals_and_its_coverage():
    rng = np.random.default_rng(1)
    pred = rng.uniform(2, 40, 20000)
    actual = np.clip(pred + rng.normal(0, 3 + pred / 10, len(pred)), 0, 60)
    table = fit_uncertainty_table(pred, actual)
    assert table["table"]["34+"]["std"] > table["table"]["<10"]["std"]  # spread grows with predicted minutes here
    q = apply_uncertainty(np.array([30.0]), table)
    assert q.loc[0, "p10"] < q.loc[0, "p50"] < q.loc[0, "p90"]
    cov = interval_coverage(pred, actual, table)
    assert cov["empirical_80"] == pytest.approx(0.80, abs=0.02)
    assert cov["empirical_50"] == pytest.approx(0.50, abs=0.02)


def test_segments_use_pre_game_information():
    f = pd.DataFrame(
        {
            "started_last": [1.0, 0.0], "min_mean10": [33.0, 8.0], "min_std10": [2.0, np.nan], "season_type": ["regular", "playoffs"],
            "games_with_team": [40.0, 0.0], "career_games": [82.0, 30.0], "traded": [0.0, 1.0], "team_games_missed": [0.0, 6.0],
            "season_games": [20.0, 2.0], "back_to_back": [0.0, 1.0], "actual_started": [0.0, 1.0],
        }
    )
    seg = ev.add_segments(f)
    assert seg.loc[0, "seg_role"] == "starter_last_game"  # from the PRIOR game, not actual_started
    assert seg.loc[1, "seg_team_change"] == "first_game_new_team"
    assert seg.loc[1, "seg_absence"] == "returning_5+_team_games_missed"
    assert str(seg.loc[1, "seg_stability"]).startswith("volatile")
