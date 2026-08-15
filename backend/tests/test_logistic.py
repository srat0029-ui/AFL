import random
import warnings
from datetime import datetime, timedelta, timezone

import pytest

from app.modelling.features import STATS_FEATURE_NAMES, MatchFeatureRow
from app.modelling.logistic import LogisticConfig, feature_matrix, fit_logistic_model, predict

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


def _synthetic_rows(n: int, season_year: int = 2016, seed: int = 0) -> list[MatchFeatureRow]:
    """Rows where a single feature ("form_diff_5") is genuinely predictive
    (positive -> home more likely to win) plus noise on the rest, so a
    fitted model has real signal to find."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        signal = rng.uniform(-1, 1)
        home_win = rng.random() < (0.5 + 0.3 * signal)
        outcome = 1.0 if home_win else 0.0
        features = {name: rng.uniform(-1, 1) for name in STATS_FEATURE_NAMES}
        features["form_diff_5"] = signal
        rows.append(_row(i, season_year, outcome, feature_overrides=features))
    return rows


def test_feature_matrix_converts_none_to_nan():
    rows = [_row(1, 2020, 1.0, feature_overrides={"form_diff_5": None})]
    X = feature_matrix(rows, STATS_FEATURE_NAMES)
    import math
    assert math.isnan(X[0][STATS_FEATURE_NAMES.index("form_diff_5")])


def test_fit_and_predict_produces_valid_probabilities():
    rows = _synthetic_rows(200)
    config = LogisticConfig(feature_names=STATS_FEATURE_NAMES, C=1.0)
    pipeline = fit_logistic_model(rows, config)
    preds = predict(pipeline, rows, STATS_FEATURE_NAMES)
    assert len(preds) == len(rows)
    assert all(0.0 <= p.home_win_probability <= 1.0 for p in preds)


def test_model_learns_the_real_signal():
    """With a genuinely predictive feature, the fitted model's probability
    should correlate with it — a very high form_diff_5 should predict a
    home win probability well above 50%."""
    rows = _synthetic_rows(400)
    config = LogisticConfig(feature_names=STATS_FEATURE_NAMES, C=1.0)
    pipeline = fit_logistic_model(rows, config)

    strong_home = _row(9001, 2016, 1.0, feature_overrides={**{n: 0.0 for n in STATS_FEATURE_NAMES}, "form_diff_5": 5.0})
    strong_away = _row(9002, 2016, 1.0, feature_overrides={**{n: 0.0 for n in STATS_FEATURE_NAMES}, "form_diff_5": -5.0})
    preds = predict(pipeline, [strong_home, strong_away], STATS_FEATURE_NAMES)
    assert preds[0].home_win_probability > 0.6
    assert preds[1].home_win_probability < 0.4


def test_draws_excluded_from_fitting_but_not_from_prediction():
    rows = _synthetic_rows(100) + [_row(999, 2016, 0.5)]  # one draw
    config = LogisticConfig(feature_names=STATS_FEATURE_NAMES, C=1.0)
    pipeline = fit_logistic_model(rows, config)  # must not raise despite the 0.5 label present in input
    preds = predict(pipeline, rows, STATS_FEATURE_NAMES)
    draw_pred = next(p for p in preds if p.match_id == 999)
    assert 0.0 <= draw_pred.home_win_probability <= 1.0  # model still scores the drawn match


def test_fitting_is_deterministic_given_same_random_state():
    rows = _synthetic_rows(150)
    config = LogisticConfig(feature_names=STATS_FEATURE_NAMES, C=1.0, random_state=7)
    pipeline_1 = fit_logistic_model(rows, config)
    pipeline_2 = fit_logistic_model(rows, config)
    preds_1 = predict(pipeline_1, rows, STATS_FEATURE_NAMES)
    preds_2 = predict(pipeline_2, rows, STATS_FEATURE_NAMES)
    assert [p.home_win_probability for p in preds_1] == [p.home_win_probability for p in preds_2]


def test_predict_on_empty_rows_returns_empty_list():
    rows = _synthetic_rows(50)
    config = LogisticConfig(feature_names=STATS_FEATURE_NAMES, C=1.0)
    pipeline = fit_logistic_model(rows, config)
    assert predict(pipeline, [], STATS_FEATURE_NAMES) == []


def test_stronger_regularisation_shrinks_coefficients_toward_zero():
    rows = _synthetic_rows(300)
    weak_reg = fit_logistic_model(rows, LogisticConfig(feature_names=STATS_FEATURE_NAMES, C=100.0))
    strong_reg = fit_logistic_model(rows, LogisticConfig(feature_names=STATS_FEATURE_NAMES, C=0.001))
    weak_coefs = weak_reg.named_steps["logreg"].coef_[0]
    strong_coefs = strong_reg.named_steps["logreg"].coef_[0]
    import numpy as np
    assert np.abs(strong_coefs).sum() < np.abs(weak_coefs).sum()
