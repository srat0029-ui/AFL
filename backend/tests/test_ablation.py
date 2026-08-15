import random
import warnings
from datetime import datetime, timedelta, timezone

from app.modelling.ablation import (
    permutation_importance,
    run_feature_group_ablation,
    run_single_feature_ablation,
    standardized_coefficients,
)
from app.modelling.features import ELO_FEATURE_NAME, STATS_FEATURE_NAMES, STATS_PLUS_ELO_FEATURE_NAMES, MatchFeatureRow
from app.modelling.logistic import LogisticConfig, fit_logistic_model

warnings.filterwarnings("ignore", category=FutureWarning)


def _row(match_id, season_year, outcome, feature_overrides=None) -> MatchFeatureRow:
    features = {name: 0.0 for name in STATS_PLUS_ELO_FEATURE_NAMES}
    if feature_overrides:
        features.update(feature_overrides)
    return MatchFeatureRow(
        match_id=match_id, season_year=season_year,
        scheduled_start=datetime(season_year, 3, 1, tzinfo=timezone.utc) + timedelta(days=match_id),
        home_team_id=1, away_team_id=2, actual_home_outcome=outcome,
        features=features, has_full_history=True,
    )


def _synthetic_rows(n: int, season_year: int, seed: int, signal_feature: str = "form_diff_5", strength: float = 0.3):
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        signal = rng.uniform(-1, 1)
        outcome = 1.0 if rng.random() < (0.5 + strength * signal) else 0.0
        features = {name: rng.uniform(-1, 1) for name in STATS_PLUS_ELO_FEATURE_NAMES}
        features[signal_feature] = signal
        rows.append(_row(i, season_year, outcome, feature_overrides=features))
    return rows


def test_standardized_coefficients_returns_one_value_per_feature():
    rows = _synthetic_rows(200, 2016, seed=0)
    pipeline = fit_logistic_model(rows, LogisticConfig(feature_names=STATS_FEATURE_NAMES, C=1.0))
    coefs = standardized_coefficients(pipeline, STATS_FEATURE_NAMES)
    assert set(coefs.keys()) == set(STATS_FEATURE_NAMES)
    assert all(isinstance(v, float) for v in coefs.values())


def test_predictive_feature_gets_larger_coefficient_than_noise_feature():
    rows = _synthetic_rows(500, 2016, seed=0, signal_feature="form_diff_5", strength=0.4)
    pipeline = fit_logistic_model(rows, LogisticConfig(feature_names=STATS_FEATURE_NAMES, C=1.0))
    coefs = standardized_coefficients(pipeline, STATS_FEATURE_NAMES)
    assert abs(coefs["form_diff_5"]) > abs(coefs["tackle_differential_diff"])


def test_permutation_importance_ranks_signal_feature_highest():
    rows = _synthetic_rows(500, 2016, seed=1, signal_feature="clearance_differential_diff", strength=0.4)
    pipeline = fit_logistic_model(rows, LogisticConfig(feature_names=STATS_FEATURE_NAMES, C=1.0))
    importances = permutation_importance(pipeline, rows, STATS_FEATURE_NAMES, n_repeats=5)
    assert importances["clearance_differential_diff"] > importances["tackle_differential_diff"]


def test_permutation_importance_empty_rows_returns_nan():
    rows = _synthetic_rows(100, 2016, seed=0)
    pipeline = fit_logistic_model(rows, LogisticConfig(feature_names=STATS_FEATURE_NAMES, C=1.0))
    importances = permutation_importance(pipeline, [], STATS_FEATURE_NAMES)
    import math
    assert all(math.isnan(v) for v in importances.values())


def test_feature_group_ablation_covers_elo_only_and_all_groups():
    tune_rows = _synthetic_rows(300, 2016, seed=0, signal_feature=ELO_FEATURE_NAME, strength=0.4)
    eval_rows = _synthetic_rows(150, 2019, seed=1, signal_feature=ELO_FEATURE_NAME, strength=0.4)

    results = run_feature_group_ablation(tune_rows, eval_rows, C=1.0)

    labels = {r.label for r in results}
    assert "elo_only" in labels
    assert "elo_plus_all_stats" in labels
    assert any(label.startswith("elo_plus_") for label in labels if label != "elo_plus_all_stats")
    assert all(r.n_eval == len(eval_rows) for r in results)


def test_feature_group_ablation_elo_only_has_zero_delta_vs_itself():
    tune_rows = _synthetic_rows(300, 2016, seed=0)
    eval_rows = _synthetic_rows(150, 2019, seed=1)
    results = run_feature_group_ablation(tune_rows, eval_rows, C=1.0)
    elo_only = next(r for r in results if r.label == "elo_only")
    assert elo_only.brier_vs_elo_alone == 0.0


def test_single_feature_ablation_returns_delta_per_feature():
    tune_rows = _synthetic_rows(300, 2016, seed=0)
    eval_rows = _synthetic_rows(150, 2019, seed=1)
    deltas = run_single_feature_ablation(tune_rows, eval_rows, STATS_FEATURE_NAMES, C=1.0)
    assert set(deltas.keys()) == set(STATS_FEATURE_NAMES)
    assert all(isinstance(v, float) for v in deltas.values())
